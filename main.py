import logging
import uuid
import csv
import io
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Request, HTTPException, Depends, Form, UploadFile, File
from pydantic import ValidationError
from sqlalchemy.orm import Session

import config
import shippingbo_client
from models import ShippingBoOrderWebhook
from mapping import build_movu_order
from database import get_db
from db_models import OrderMapping, WebhookLog, User, PreparationRun, PreparationRunPack, InboundRequest
from auth import (
    hash_password, verify_password, create_session_token, get_current_user,
    SESSION_COOKIE_NAME, NotAuthenticatedException,
)

from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("atome_middleware")

app = FastAPI(title="Atome3D Order Middleware")
templates = Jinja2Templates(directory="templates")

# Full state enum, confirmed from ShippingBo's own PreparationRun API docs
# (Aug 26) — used for the state filter dropdown in the interface.
PREPARATION_RUN_STATES = [
    "new", "packages_generated", "ps_generated", "ps_downloaded",
    "ps_printed", "shipped", "archived",
]

# InboundRequest status lifecycle — "sent" means successfully created in
# Movu, NOT yet physically complete. Real completion comes from
# /webhook/movu notifications, updating the same row.
INBOUND_STATUSES = ["requested", "dry_run", "sent", "in_progress", "completed", "cancelled", "failed"]

# Matches "preparation_run_summary_6397693.csv" or the "(1)"/"(2)" browser
# duplicate-download suffix variant — captures just the numeric run ID,
# stopping at the first non-digit character either way.
PREPARATION_RUN_FILENAME_PATTERN = re.compile(r"preparation_run_summary_(\d+)")

PARIS_TZ = ZoneInfo("Europe/Paris")


def format_paris_time(dt):
    """Jinja2 filter: render a UTC-aware datetime in France's local time."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PARIS_TZ).strftime("%d/%m/%Y %H:%M")


templates.env.filters["paris_time"] = format_paris_time


@app.exception_handler(NotAuthenticatedException)
async def not_authenticated_handler(request: Request, exc: NotAuthenticatedException):
    """Redirects browser page visits to /login instead of showing raw JSON."""
    return RedirectResponse(url="/login", status_code=303)

# Prometheus metrics at /metrics — request counts, latencies, etc, auto
# instrumented. IMPORTANT: this endpoint must NEVER be exposed publicly on
# movu.izylog.com (security requirement) — blocked at the Caddy level, see
# Caddyfile. Only reachable internally (VM101 localhost / docker network),
# which is where Prometheus itself scrapes it from.
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Gauge
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# --- Stale inbound mission safety net -----------------------------------
# Real gap, observed directly (Aug 27): a genuinely stuck Movu mission
# never emitted a failure webhook — no OrderLineErrored, no OrderAborted,
# nothing. Since webhook completeness can't be trusted for this, compute
# the stale count LIVE on every Prometheus scrape (via set_function, no
# separate scheduler needed) — a Grafana alert watches this metric.
stale_inbound_gauge = Gauge(
    "stale_inbound_requests_total",
    "InboundRequests stuck at in_progress beyond STALE_INBOUND_THRESHOLD_MINUTES",
)


def _compute_stale_inbound_count():
    from database import SessionLocal
    db = SessionLocal()
    try:
        threshold = datetime.now(timezone.utc) - timedelta(minutes=config.STALE_INBOUND_THRESHOLD_MINUTES)
        return db.query(InboundRequest).filter(
            InboundRequest.status == "in_progress",
            InboundRequest.created_at < threshold,
        ).count()
    finally:
        db.close()


stale_inbound_gauge.set_function(_compute_stale_inbound_count)

# Movu notification types that mean "an inbound handling unit finished
# being stored" — this is when stock needs syncing to ShippingBo's
# aggregate MOVU emplacement. Confirmed from the functional design doc's
# webhook list (section 10, annex 16.3).
INBOUND_COMPLETE_NOTIFICATION_TYPES = {"HandlingUnitStored", "OrderLineProcessed"}


@app.get("/health")
def health():
    # Deliberately minimal — no DRY_RUN flags or business logic details.
    # This endpoint has no authentication (needed for basic liveness
    # checks), so nothing operationally sensitive belongs here. Detailed
    # status is available via `docker-compose logs` or Grafana, both
    # already access-controlled.
    return {"status": "ok"}


@app.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})


@app.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter_by(email=email.strip().lower()).first()

    if not user or not user.is_active or not verify_password(password, user.password_hash):
        logger.warning("Failed login attempt for email=%s", email)
        return templates.TemplateResponse(
            request=request, name="login.html", context={"error": "Invalid email or password."}, status_code=401
        )

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    token = create_session_token(user.id)
    response = RedirectResponse(url="/preparation", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,  # only sent over HTTPS — fine, Caddy handles TLS
        samesite="lax",
        max_age=60 * 60 * 24,  # 24h, matches SESSION_EXPIRE_HOURS in auth.py
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/mise-en-stock")
def mise_en_stock_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    status: str = "",
    page: int = 1,
    per_page: int = 25,
):
    if per_page not in (25, 50, 100):
        per_page = 25
    page = max(1, page)

    query = db.query(InboundRequest)
    if status:
        query = query.filter(InboundRequest.status == status)

    total_count = query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)

    recent = (
        query.order_by(InboundRequest.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="mise_en_stock.html",
        context={
            "user_email": current_user.email,
            "recent": recent,
            "selected_status": status,
            "statuses": INBOUND_STATUSES,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total_count": total_count,
        },
    )


@app.post("/mise-en-stock/scan")
async def mise_en_stock_scan(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    handling_unit_id: str = Form(...),
    gate: str = Form(...),
):
    """
    Colleague scans a physical pack's barcode, selects which physical gate
    they're standing at, clicks the button — creates a Movu "In" order for
    that specific handling unit at that specific gate. Gate is a REQUIRED
    field for inbound orders per Movu's own docs (unlike outbound/Cycle,
    where it's optional and auto-assigned) — only the colleague physically
    present knows which gate they're using, so this can't be hardcoded.
    """
    handling_unit_id = handling_unit_id.strip()
    inbound_id = str(uuid.uuid4())

    movu_payload = {
        "id": f"IN-{handling_unit_id}-{inbound_id[:8]}",
        "type": "In",
        "due": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
        "released": True,  # inbound orders auto-execute, confirmed from live test data
        "terminal": "MPS3",
        "orderLines": [
            {
                "handlingUnitId": handling_unit_id,
                "gate": gate,
                "barcodes": [handling_unit_id],
                # Confirmed field name from Atome 3D.docx chapter 7 + real
                # swagger.json example (Aug 27) — "categories", NOT
                # "storageCategories". The old key was silently wrong
                # this whole time; only harmless because DRY_RUN=true
                # meant it was never actually sent to Movu.
                "storageProfile": {"stockId": "1", "quality": "", "categories": ["B"]},
            }
        ],
    }

    record = InboundRequest(
        id=inbound_id,
        handling_unit_id=handling_unit_id,
        gate=gate,
        requested_by_email=current_user.email,
    )

    if config.DRY_RUN_INBOUND:
        logger.info("[DRY_RUN_INBOUND] Would POST inbound 'In' order to Movu OPS: %s", movu_payload)
        record.status = "dry_run"
        record.movu_order_id = movu_payload["id"]
    else:
        url = f"{config.MOVU_OPS_BASE_URL}/api/v3/orders"
        headers = {"x-api-key": config.MOVU_OPS_API_KEY} if config.MOVU_OPS_API_KEY else {}
        async with httpx.AsyncClient(timeout=10, verify=config.MOVU_OPS_VERIFY_SSL) as client:
            try:
                resp = await client.post(url, json=movu_payload, headers=headers)
                resp.raise_for_status()
                # "sent" = successfully created in Movu, NOT yet physically
                # complete. Real completion comes later via /webhook/movu
                # (OrderFinished etc), which updates this same row.
                record.status = "sent"
                record.movu_order_id = movu_payload["id"]
            except httpx.HTTPError as e:
                logger.error("Failed to create inbound order for %s: %s", handling_unit_id, e)
                record.status = "failed"
                record.error_message = str(e)

    db.add(record)
    db.commit()

    return RedirectResponse(url="/mise-en-stock", status_code=303)


@app.post("/mise-en-stock/{inbound_id}/cancel")
async def mise_en_stock_cancel(
    inbound_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Aborts the Movu order for this inbound request — frees any location
    reservation Movu had made for it. Same mechanism used weeks ago to
    unblock a stuck test order (POST /api/v3/orders/{id}/abort).
    """
    record = db.query(InboundRequest).filter_by(id=inbound_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Inbound request not found")

    if record.status in ("cancelled", "completed", "failed"):
        # Already in a terminal state, nothing to cancel.
        return RedirectResponse(url="/mise-en-stock", status_code=303)

    if record.status == "dry_run":
        record.status = "cancelled"
    else:
        url = f"{config.MOVU_OPS_BASE_URL}/api/v3/orders/{record.movu_order_id}/abort"
        headers = {"x-api-key": config.MOVU_OPS_API_KEY} if config.MOVU_OPS_API_KEY else {}
        async with httpx.AsyncClient(timeout=10, verify=config.MOVU_OPS_VERIFY_SSL) as client:
            try:
                resp = await client.post(url, headers=headers)
                resp.raise_for_status()
                record.status = "cancelled"
            except httpx.HTTPError as e:
                logger.error("Failed to abort inbound order %s: %s", record.movu_order_id, e)
                record.error_message = f"Cancel failed: {e}"

    db.commit()
    return RedirectResponse(url="/mise-en-stock", status_code=303)


@app.get("/preparation")
def preparation_list(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    state: str = "",
    search_id: str = "",
    page: int = 1,
    per_page: int = 25,
):
    if per_page not in (25, 50, 100):
        per_page = 25
    page = max(1, page)

    query = db.query(PreparationRun)
    if state:
        query = query.filter(PreparationRun.state == state)
    if search_id:
        query = query.filter(PreparationRun.id.ilike(f"%{search_id.strip()}%"))

    total_count = query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)

    runs = (
        query.order_by(PreparationRun.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="internal_runs_list.html",
        context={
            "user_email": current_user.email,
            "runs": runs,
            "states": PREPARATION_RUN_STATES,
            "selected_state": state,
            "search_id": search_id,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total_count": total_count,
        },
    )


@app.get("/preparation/{run_id}/upload")
def preparation_upload_form(
    run_id: str, request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    run = db.query(PreparationRun).filter_by(id=run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Preparation run not found")
    return templates.TemplateResponse(
        request=request, name="preparation_upload.html", context={"user_email": current_user.email, "run": run, "error": None}
    )


@app.post("/preparation/{run_id}/upload")
async def preparation_upload_csv(
    run_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
):
    """
    Parses the uploaded CSV (Sku, Désignation, EAN-13, Emplacement, Qté —
    real ShippingBo export format, confirmed Aug 27) into
    PreparationRunPack rows. Cross-checks each row's Emplacement against
    Movu's LIVE handling units (one API call for the whole file, not per
    row) to set is_movu_stocked — self-updating, no manually maintained
    whitelist. Non-Movu emplacements (e.g. "0.A001"-style, the OTHER
    warehouse's own location codes) are kept for visibility but excluded
    from any mission trigger.

    Two safety checks before parsing:
    - Filename sanity check: if the filename matches ShippingBo's export
      naming pattern but the embedded run ID doesn't match the run being
      uploaded to, reject with a clear error rather than silently
      loading the wrong run's data.
    - Re-upload safety: any existing PreparationRunPack rows for this run
      are deleted first, so uploading a corrected CSV cleanly REPLACES
      the previous (possibly wrong) data instead of appending duplicates
      on top of it.
    """
    run = db.query(PreparationRun).filter_by(id=run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Preparation run not found")

    filename_match = PREPARATION_RUN_FILENAME_PATTERN.search(file.filename or "")
    if filename_match and filename_match.group(1) != run_id:
        logger.warning(
            "Upload rejected: filename '%s' suggests run %s, but uploading to run %s",
            file.filename, filename_match.group(1), run_id,
        )
        return templates.TemplateResponse(
            request=request,
            name="preparation_upload.html",
            context={
                "user_email": current_user.email,
                "run": run,
                "error": (
                    f"Le nom du fichier indique le run {filename_match.group(1)}, "
                    f"mais vous êtes sur le run {run_id}. Vérifiez que c'est le bon fichier avant de réessayer."
                ),
            },
            status_code=400,
        )

    raw_bytes = await file.read()
    text = raw_bytes.decode("utf-8-sig")  # utf-8-sig strips a BOM if Excel added one
    reader = csv.DictReader(io.StringIO(text))

    # Re-upload safety: replace, don't append. Without this, re-uploading
    # a corrected CSV after an initial mistake would just duplicate every
    # row on top of the wrong ones already in the table.
    deleted_count = db.query(PreparationRunPack).filter_by(preparation_run_id=run_id).delete()
    if deleted_count:
        logger.info("Cleared %d existing pack rows for run %s before re-upload", deleted_count, run_id)

    # Fetch Movu's live handling units ONCE for the whole upload, not per row.
    movu_handling_unit_ids = set()
    headers = {"x-api-key": config.MOVU_OPS_API_KEY} if config.MOVU_OPS_API_KEY else {}
    async with httpx.AsyncClient(timeout=10, verify=config.MOVU_OPS_VERIFY_SSL) as client:
        try:
            resp = await client.get(f"{config.MOVU_OPS_BASE_URL}/api/v3/handlingunits", headers=headers)
            resp.raise_for_status()
            movu_handling_unit_ids = {hu["id"] for hu in resp.json()}
        except httpx.HTTPError as e:
            logger.error("Failed to fetch Movu handling units for cross-check: %s", e)
            # Continue anyway — rows just won't be matched, safer than
            # blocking the whole upload on a transient Movu API issue.

    parsed_count = 0
    matched_count = 0
    for row in reader:
        emplacement = (row.get("Emplacement") or "").strip()
        sku = (row.get("Sku") or "").strip()
        if not emplacement or not sku:
            continue  # skip malformed/blank lines rather than error the whole upload

        try:
            quantity = int(row.get("Qté", "0").strip())
        except ValueError:
            quantity = 0

        is_movu_stocked = emplacement in movu_handling_unit_ids
        if is_movu_stocked:
            matched_count += 1

        db.add(PreparationRunPack(
            preparation_run_id=run_id,
            sku=sku,
            designation=row.get("Désignation"),
            emplacement=emplacement,
            quantity=quantity,
            is_movu_stocked=is_movu_stocked,
            movu_handling_unit_id=emplacement if is_movu_stocked else None,
        ))
        parsed_count += 1

    run.detail_uploaded = True
    run.detail_filename = file.filename
    db.commit()

    logger.info(
        "Uploaded CSV for run %s: %d rows parsed, %d matched to Movu handling units",
        run_id, parsed_count, matched_count,
    )

    return RedirectResponse(url=f"/preparation/{run_id}", status_code=303)


@app.get("/preparation/{run_id}")
def preparation_detail(
    run_id: str, request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    run = db.query(PreparationRun).filter_by(id=run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Preparation run not found")

    packs = db.query(PreparationRunPack).filter_by(preparation_run_id=run_id).order_by(PreparationRunPack.emplacement).all()

    # Group by emplacement — Movu only cares about "go get this pack,"
    # not the individual SKU lines inside it. Multiple products in the
    # same physical pack share one emplacement and must trigger exactly
    # ONE mission, not one per SKU line.
    groups = {}
    for p in packs:
        if p.emplacement not in groups:
            groups[p.emplacement] = {
                "emplacement": p.emplacement,
                "is_movu_stocked": p.is_movu_stocked,
                "movu_handling_unit_id": p.movu_handling_unit_id,
                "status": p.status,
                "representative_pack_id": p.id,  # used as the trigger target for the whole group
                "lines": [],
            }
        groups[p.emplacement]["lines"].append({
            "sku": p.sku, "designation": p.designation, "quantity": p.quantity,
        })

    return templates.TemplateResponse(
        request=request,
        name="preparation_detail.html",
        context={
            "user_email": current_user.email,
            "run": run,
            "groups": list(groups.values()),
            "total_lines": len(packs),
        },
    )


VALID_OUTBOUND_GATES = {
    "MPS1G1", "MPS1G2", "MPS1G3",
    "MPS2G1", "MPS2G2", "MPS2G3",
    "MPS3G1", "MPS3G2", "MPS3G3",
}
# NOTE: MPS1/MPS2 having exactly 3 gates each (G1-G3) is an assumption
# based on the confirmed MPS3 pattern — not independently verified for
# MPS1/MPS2. Worth confirming before trusting this list completely.


@app.post("/preparation/packs/{pack_id}/trigger")
async def preparation_trigger_pack(
    pack_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    gate: str = Form(...),
):
    """
    Triggers a Movu outbound Cycle order for one physical pack (identified
    by ONE representative PreparationRunPack row, but the resulting
    status is applied to EVERY row sharing the same emplacement — since
    multiple SKUs in the same pack must not trigger separate missions).

    Gate is a SCAN input (colleague's physical gate barcode) — terminal
    is DERIVED from the gate (e.g. "MPS1G2" -> "MPS1"). NOTE: Atome 3D
    doc section 9.1 recommends NOT specifying gate for Cycle orders
    (Movu Ops picks the best available gate, allowing better shuttle
    queueing/performance) — this is a deliberate, confirmed tradeoff:
    precise operator control over raw throughput, decided explicitly
    rather than defaulted to the doc's recommendation.

    Uses an EXPLICIT handlingUnitId — the confirmed Flow B design: Movu
    is told "go get this specific pack," not asked to resolve stock
    itself.

    IMPORTANT — released MUST be false. Confirmed from Atome 3D.docx
    section 9.1: "It is mandatory for the Atome's operation to put the
    released parameter at false, so all the bins can go to staging zone
    nearby therefore limiting travel time during picking." Since a
    released:false order sits in staging and never starts on its own,
    an immediate follow-up call to /release is required — combined into
    this single button click (confirmed choice, not a true two-phase
    staging workflow) so the operator's experience stays "one click,
    one mission," while the actual API sequence stays compliant with
    the documented requirement.
    """
    gate = gate.strip().upper()
    if gate not in VALID_OUTBOUND_GATES:
        logger.warning("Rejected outbound trigger: invalid gate '%s' (from barcode scan)", gate)
        raise HTTPException(status_code=400, detail=f"Gate scanné invalide : '{gate}'.")
    terminal = gate[:4]  # "MPS1G2" -> "MPS1"

    pack = db.query(PreparationRunPack).filter_by(id=pack_id).first()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")
    if not pack.is_movu_stocked:
        raise HTTPException(status_code=400, detail="This pack is not tracked in Movu — cannot trigger a mission")

    # Every row sharing this emplacement, within the same run, gets the
    # same order/status — they represent one physical pack.
    sibling_packs = db.query(PreparationRunPack).filter_by(
        preparation_run_id=pack.preparation_run_id, emplacement=pack.emplacement,
    ).all()

    movu_order_id = f"OUT-{pack.id[:8]}"
    movu_payload = {
        "id": movu_order_id,
        "type": "Cycle",
        "due": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
        "priority": None,
        "released": False,  # MANDATORY false per Atome 3D.docx 9.1 — see docstring
        "terminal": terminal,
        "orderLines": [{"handlingUnitId": pack.movu_handling_unit_id, "gate": gate, "slot": None}],
    }

    if config.DRY_RUN_OUTBOUND:
        logger.info(
            "[DRY_RUN_OUTBOUND] Would POST outbound order (released=false) then immediately "
            "POST /api/v3/orders/%s/release to Movu OPS: %s",
            movu_order_id, movu_payload,
        )
        new_status = "dry_run"
    else:
        headers = {"x-api-key": config.MOVU_OPS_API_KEY} if config.MOVU_OPS_API_KEY else {}
        async with httpx.AsyncClient(timeout=10, verify=config.MOVU_OPS_VERIFY_SSL) as client:
            try:
                resp = await client.post(f"{config.MOVU_OPS_BASE_URL}/api/v3/orders", json=movu_payload, headers=headers)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.error("Failed to create outbound order for pack %s: %s", pack_id, e)
                new_status = "failed"
            else:
                # Order created successfully but sitting in staging
                # (released:false) — must explicitly release it now.
                try:
                    release_resp = await client.post(
                        f"{config.MOVU_OPS_BASE_URL}/api/v3/orders/{movu_order_id}/release", headers=headers
                    )
                    release_resp.raise_for_status()
                    new_status = "sent"
                except httpx.HTTPError as e:
                    # Order EXISTS in Movu (staged) but release failed —
                    # this needs manual attention in Movu's own UI, not
                    # a silent retry, since we don't want to double-release.
                    logger.error(
                        "Order %s created successfully but /release FAILED for pack %s: %s. "
                        "Order is stuck in staging — check Movu Ops UI directly.",
                        movu_order_id, pack_id, e,
                    )
                    new_status = "failed"

    for sibling in sibling_packs:
        sibling.status = new_status
        sibling.movu_order_id = movu_order_id

    db.commit()
    return RedirectResponse(url=f"/preparation/{pack.preparation_run_id}", status_code=303)


@app.post("/preparation/packs/{pack_id}/confirm-picking")
async def preparation_confirm_picking(
    pack_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Colleague clicks this once they've physically finished picking from
    a presented tote — sends it back to storage. Confirmed from Atome
    3D.docx 9.3: "OrderLinePresented: At this point you will have to
    call POST /api/v3/terminals/{terminalId}/gate/{gateId}/release to
    store the tote back again." Only a human knows when picking is
    actually done — this can never be automatic.
    """
    pack = db.query(PreparationRunPack).filter_by(id=pack_id).first()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")
    if pack.status != "presented":
        raise HTTPException(status_code=400, detail="This pack isn't in 'presented' state — nothing to confirm")
    if not pack.presented_terminal_id or not pack.presented_gate_id:
        raise HTTPException(status_code=400, detail="Missing terminal/gate info — was OrderLinePresented ever received?")

    sibling_packs = db.query(PreparationRunPack).filter_by(
        preparation_run_id=pack.preparation_run_id, emplacement=pack.emplacement,
    ).all()

    if config.DRY_RUN_OUTBOUND:
        logger.info(
            "[DRY_RUN_OUTBOUND] Would POST /api/v3/terminals/%s/gate/%s/release for pack %s",
            pack.presented_terminal_id, pack.presented_gate_id, pack_id,
        )
        new_status = "dry_run"
    else:
        headers = {"x-api-key": config.MOVU_OPS_API_KEY} if config.MOVU_OPS_API_KEY else {}
        url = f"{config.MOVU_OPS_BASE_URL}/api/v3/terminals/{pack.presented_terminal_id}/gate/{pack.presented_gate_id}/release"
        async with httpx.AsyncClient(timeout=10, verify=config.MOVU_OPS_VERIFY_SSL) as client:
            try:
                resp = await client.post(url, headers=headers)
                resp.raise_for_status()
                new_status = "returning"
            except httpx.HTTPError as e:
                logger.error("Failed to release gate for pack %s: %s", pack_id, e)
                new_status = "failed"

    for sibling in sibling_packs:
        sibling.status = new_status

    db.commit()
    return RedirectResponse(url=f"/preparation/{pack.preparation_run_id}", status_code=303)


@app.post("/webhook/order")
async def receive_order_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receives ShippingBo's order/status webhook. Only forwards to Movu if
    the order's new state matches TRIGGER_STATES (still unconfirmed — see
    config.py). Records an order_mapping row so /webhook/movu can later
    match Movu's notifications back to this ShippingBo order.
    """
    # --- Security requirement #1: authenticate the webhook first, before
    # touching the body at all. ShippingBo's "Header libre" scheme sends a
    # shared-secret value as a custom header on every call. Fail-closed:
    # if SHIPPINGBO_WEBHOOK_HEADER_VALUE isn't configured, every request
    # gets rejected rather than silently accepted.
    incoming_secret = request.headers.get(config.SHIPPINGBO_WEBHOOK_HEADER_NAME)
    if not config.SHIPPINGBO_WEBHOOK_HEADER_VALUE or incoming_secret != config.SHIPPINGBO_WEBHOOK_HEADER_VALUE:
        logger.warning(
            "Rejected /webhook/order call: missing or invalid '%s' header",
            config.SHIPPINGBO_WEBHOOK_HEADER_NAME,
        )
        raise HTTPException(status_code=401, detail="Invalid or missing authentication header")

    raw_body = await request.json()
    logger.info(
        "Received order webhook: hook_id=%s order_id=%s state=%s (from=%s)",
        raw_body.get("hook_id"),
        raw_body.get("object", {}).get("id"),
        raw_body.get("object", {}).get("state"),
        raw_body.get("additional_data", {}).get("from"),
    )

    try:
        webhook = ShippingBoOrderWebhook.model_validate(raw_body)
    except ValidationError as e:
        logger.error("Payload failed validation: %s", e)
        raise HTTPException(status_code=422, detail=e.errors())

    order = webhook.object

    # Idempotency: ShippingBo's webhook has no notification_id like Movu's
    # does. Build a synthetic key from what's available. NOTE: this is an
    # assumption, not confirmed against real duplicate-delivery behavior —
    # revisit if ShippingBo's docs describe a proper webhook delivery ID.
    synthetic_notification_id = f"shippingbo-{webhook.hook_id}-{order.id}-{order.state}"
    existing_log = db.query(WebhookLog).filter_by(notification_id=synthetic_notification_id).first()
    if existing_log:
        logger.info("Duplicate ShippingBo webhook (%s) — already processed, skipping.", synthetic_notification_id)
        return {"status": "duplicate_skipped"}

    db.add(WebhookLog(
        notification_id=synthetic_notification_id,
        source="shippingbo",
        notification_type=f"order.{order.state}",
        payload=raw_body,
        processed=False,
    ))
    db.commit()

    if order.state not in config.TRIGGER_STATES:
        logger.info(
            "Order %s state '%s' is not in TRIGGER_STATES %s — ignoring.",
            order.id, order.state, config.TRIGGER_STATES,
        )
        return {"status": "ignored", "reason": f"state '{order.state}' is not a trigger state"}

    result = build_movu_order(order)
    movu_payload = result["movu_payload"]

    if not movu_payload["orderDemands"]:
        logger.warning(
            "Order %s produced zero orderDemands — nothing to send (either no items had a "
            "product_ref, or none are in MOVU_STOCKED_PRODUCT_REFS).",
            order.id,
        )
        return {"status": "no_demands", "skipped_items": result["skipped_items"]}

    if config.DRY_RUN_ORDERS:
        logger.info("[DRY_RUN_ORDERS] Would POST to Movu OPS: %s", movu_payload)
        return {
            "status": "dry_run",
            "movu_payload": movu_payload,
            "skipped_items": result["skipped_items"],
        }

    url = f"{config.MOVU_OPS_BASE_URL}/api/v3/orders"
    headers = {"x-api-key": config.MOVU_OPS_API_KEY} if config.MOVU_OPS_API_KEY else {}
    async with httpx.AsyncClient(timeout=10, verify=config.MOVU_OPS_VERIFY_SSL) as client:
        try:
            resp = await client.post(url, json=movu_payload, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("Failed to forward order to Movu OPS: %s", e)
            raise HTTPException(status_code=502, detail=f"Movu OPS error: {e}")

    # Record the mapping so /webhook/movu can find this order later.
    db.add(OrderMapping(
        id=str(uuid.uuid4()),
        xano_order_id=str(order.id),
        movu_order_id=movu_payload["id"],
        terminal_id=config.MOVU_TERMINAL_ID,
        current_state="Created",
    ))
    db.commit()

    return {
        "status": "forwarded",
        "movu_response": resp.json() if resp.content else None,
        "skipped_items": result["skipped_items"],
    }


@app.post("/webhook/preparation")
async def receive_preparation_webhook(request: Request, db: Session = Depends(get_db)):
    """
    STUB — receives ShippingBo's PreparationRun webhook. Real payload shape
    is not yet known (new topic, no sample captured). For now: authenticate,
    log the raw body to webhook_log so we can inspect it once real traffic
    arrives, and return 200. No processing logic yet — that comes once we
    see a real payload and can build a proper model, mirroring how
    ShippingBoOrderWebhook was built from a real sample.
    """
    incoming_secret = request.headers.get(config.SHIPPINGBO_WEBHOOK_HEADER_NAME)
    if not config.SHIPPINGBO_WEBHOOK_HEADER_VALUE or incoming_secret != config.SHIPPINGBO_WEBHOOK_HEADER_VALUE:
        logger.warning(
            "Rejected /webhook/preparation call: missing or invalid '%s' header",
            config.SHIPPINGBO_WEBHOOK_HEADER_NAME,
        )
        raise HTTPException(status_code=401, detail="Invalid or missing authentication header")

    raw_body = await request.json()
    logger.info("Received PreparationRun webhook (raw, shape not yet known): %s", raw_body)

    db.add(WebhookLog(
        notification_id=str(uuid.uuid4()),
        source="shippingbo_preparation",
        notification_type="preparation_run",
        payload=raw_body,
        processed=False,
    ))

    # Upsert into preparation_run so the interface list reflects it.
    # Only meaningful once real (non-empty) payloads start arriving —
    # {}-body validation pings from ShippingBo are logged above but
    # correctly skipped here (no "object" key to extract from).
    obj = raw_body.get("object")
    if obj and obj.get("id"):
        run_id = str(obj["id"])
        existing_run = db.query(PreparationRun).filter_by(id=run_id).first()
        if existing_run:
            existing_run.state = obj.get("state", existing_run.state)
            existing_run.package_count = obj.get("preparation_packages_count", existing_run.package_count)
        else:
            db.add(PreparationRun(
                id=run_id,
                state=obj.get("state", "unknown"),
                package_count=obj.get("preparation_packages_count"),
            ))

    db.commit()

    return {"status": "logged_stub"}


# Maps Movu notification types to InboundRequest status. Not every
# notification type is listed — only ones that actually change our
# tracked state; anything else leaves the row untouched.
INBOUND_NOTIFICATION_STATUS_MAP = {
    "OrderStarted": "in_progress",
    "OrderLineStarted": "in_progress",
    "OrderActive": "in_progress",
    "OrderLineActive": "in_progress",
    "OrderLineReleased": "returning",  # tote is on its way back to storage
    "OrderFinished": "completed",
    "OrderLineFinished": "completed",
    "OrderProcessed": "completed",
    "OrderLineProcessed": "completed",
    "HandlingUnitStored": "completed",
    "OrderAborted": "cancelled",
    "OrderLineErrored": "failed",
}

# OrderLinePresented is handled SEPARATELY, not via the generic map above
# — it needs to capture terminalId/gateId from the payload (required for
# the later /release call), not just set a status string.
PRESENTED_NOTIFICATION_TYPE = "OrderLinePresented"


@app.post("/webhook/movu")
async def receive_movu_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receives Movu OPS's own notifications (order lifecycle events). Two
    jobs: keep order_mapping.current_state up to date, and — for inbound
    completion specifically — sync stock into ShippingBo's aggregate MOVU
    emplacement, but ONLY if this middleware itself created the inbound
    order (i.e. a matching order_mapping row exists). If no mapping is
    found, this handling unit was stored outside the middleware's
    knowledge (e.g. manual entry via the Movu Ops UI) — we do NOT guess
    the SKU/quantity in that case, we skip and log it.
    """
    # --- Security requirement: authenticate Movu's own webhook calls too,
    # same shared-secret-header pattern as ShippingBo. Registered with Movu
    # via register_movu_webhook.py (sets this header in Movu's own
    # WebhookRegistrationDetails.httpHeaders). Fail-closed, same as above.
    incoming_secret = request.headers.get(config.MOVU_WEBHOOK_HEADER_NAME)
    if not config.MOVU_WEBHOOK_HEADER_VALUE or incoming_secret != config.MOVU_WEBHOOK_HEADER_VALUE:
        logger.warning(
            "Rejected /webhook/movu call: missing or invalid '%s' header",
            config.MOVU_WEBHOOK_HEADER_NAME,
        )
        raise HTTPException(status_code=401, detail="Invalid or missing authentication header")

    raw_body = await request.json()
    notifications = raw_body.get("notifications", [])
    results = []

    for notif in notifications:
        notification_id = notif.get("notificationId") or str(uuid.uuid4())
        notification_type = notif.get("notificationType")
        payload = notif.get("payload", {})

        existing_log = db.query(WebhookLog).filter_by(notification_id=notification_id).first()
        if existing_log:
            logger.info("Duplicate Movu notification %s — already processed, skipping.", notification_id)
            results.append({"notification_id": notification_id, "status": "duplicate_skipped"})
            continue

        db.add(WebhookLog(
            notification_id=notification_id,
            source="movu",
            notification_type=notification_type,
            payload=payload,
            processed=False,
        ))
        db.commit()

        movu_order_id = payload.get("order") or payload.get("id")
        mapping = None
        if movu_order_id:
            mapping = db.query(OrderMapping).filter_by(movu_order_id=movu_order_id).first()

        if mapping:
            mapping.current_state = notification_type
            db.commit()

        # Also check InboundRequest — the mise-en-stock flow's own rows,
        # separate from OrderMapping (Flow A). A given movu_order_id only
        # ever matches one of the two tables, never both.
        if movu_order_id:
            inbound_record = db.query(InboundRequest).filter_by(movu_order_id=movu_order_id).first()
            if inbound_record and notification_type in INBOUND_NOTIFICATION_STATUS_MAP:
                inbound_record.status = INBOUND_NOTIFICATION_STATUS_MAP[notification_type]
                db.commit()

            pack_record = db.query(PreparationRunPack).filter_by(movu_order_id=movu_order_id).first()
            if pack_record:
                sibling_packs = db.query(PreparationRunPack).filter_by(movu_order_id=movu_order_id).all()

                if notification_type == PRESENTED_NOTIFICATION_TYPE:
                    # Capture terminalId/gateId — needed for the release
                    # call once the colleague confirms picking is done.
                    presented_terminal = payload.get("terminalId")
                    presented_gate = payload.get("gateId")
                    for sibling in sibling_packs:
                        sibling.status = "presented"
                        sibling.presented_terminal_id = presented_terminal
                        sibling.presented_gate_id = presented_gate
                    db.commit()
                elif notification_type in INBOUND_NOTIFICATION_STATUS_MAP:
                    for sibling in sibling_packs:
                        sibling.status = INBOUND_NOTIFICATION_STATUS_MAP[notification_type]
                    db.commit()

        if notification_type in INBOUND_COMPLETE_NOTIFICATION_TYPES:
            if mapping is None:
                logger.warning(
                    "Notification %s (%s, movu_order_id=%s) has no matching order_mapping row — "
                    "this handling unit was likely stored outside the middleware's own inbound "
                    "flow. Cannot auto-sync stock to ShippingBo without a known SKU/quantity — skipping.",
                    notification_id, notification_type, movu_order_id,
                )
                results.append({"notification_id": notification_id, "status": "no_mapping_skipped"})
            elif config.DRY_RUN_ORDERS:
                logger.info(
                    "[DRY_RUN] Would sync stock to ShippingBo MOVU emplacement for order_mapping %s.",
                    mapping.id,
                )
                results.append({"notification_id": notification_id, "status": "dry_run_stock_sync"})
            else:
                try:
                    # TODO: real stockId/quantity need to come from the
                    # original order_demand, not hardcoded — wire this up
                    # once shippingbo_client.update_movu_stock() is real.
                    shippingbo_client.update_movu_stock(stock_id="TODO", quantity_delta=0)
                    results.append({"notification_id": notification_id, "status": "stock_synced"})
                except NotImplementedError as e:
                    logger.warning("ShippingBo stock sync not implemented yet: %s", e)
                    results.append({"notification_id": notification_id, "status": "stock_sync_not_implemented"})
        else:
            results.append({"notification_id": notification_id, "status": "logged_no_action"})

        db.query(WebhookLog).filter_by(notification_id=notification_id).update({"processed": True})
        db.commit()

    return {"status": "ok", "results": results}
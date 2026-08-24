import logging
import uuid

import httpx
from fastapi import FastAPI, Request, HTTPException, Depends
from pydantic import ValidationError
from sqlalchemy.orm import Session

import config
import shippingbo_client
from models import ShippingBoOrderWebhook
from mapping import build_movu_order
from database import get_db
from db_models import OrderMapping, WebhookLog

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("atome_middleware")

app = FastAPI(title="Atome3D Order Middleware")

# Movu notification types that mean "an inbound handling unit finished
# being stored" — this is when stock needs syncing to ShippingBo's
# aggregate MOVU emplacement. Confirmed from the functional design doc's
# webhook list (section 10, annex 16.3).
INBOUND_COMPLETE_NOTIFICATION_TYPES = {"HandlingUnitStored", "OrderLineProcessed"}


@app.get("/health")
def health():
    return {"status": "ok", "dry_run": config.DRY_RUN, "trigger_states": list(config.TRIGGER_STATES)}


@app.post("/webhook/order")
async def receive_order_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receives ShippingBo's order/status webhook. Only forwards to Movu if
    the order's new state matches TRIGGER_STATES (still unconfirmed — see
    config.py). Records an order_mapping row so /webhook/movu can later
    match Movu's notifications back to this ShippingBo order.
    """
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

    if config.DRY_RUN:
        logger.info("[DRY_RUN] Would POST to Movu OPS: %s", movu_payload)
        return {
            "status": "dry_run",
            "movu_payload": movu_payload,
            "skipped_items": result["skipped_items"],
        }

    url = f"{config.MOVU_OPS_BASE_URL}/api/v3/orders"
    async with httpx.AsyncClient(timeout=10, verify=config.MOVU_OPS_VERIFY_SSL) as client:
        try:
            resp = await client.post(url, json=movu_payload)
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

        if notification_type in INBOUND_COMPLETE_NOTIFICATION_TYPES:
            if mapping is None:
                logger.warning(
                    "Notification %s (%s, movu_order_id=%s) has no matching order_mapping row — "
                    "this handling unit was likely stored outside the middleware's own inbound "
                    "flow. Cannot auto-sync stock to ShippingBo without a known SKU/quantity — skipping.",
                    notification_id, notification_type, movu_order_id,
                )
                results.append({"notification_id": notification_id, "status": "no_mapping_skipped"})
            elif config.DRY_RUN:
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
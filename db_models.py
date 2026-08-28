"""
ORM models for the middleware's own tables. As of Aug 26, these live in a
FULLY DEDICATED Postgres instance (its own container on VM101), not shared
with Movu's Postgres anymore — see docker-compose.yml's "postgres" service.

This resolves the risk flagged after Sam's email (Aug 24): Movu explicitly
stated they don't authorize direct DB access, and since they had no
official awareness the "middleware" schema existed inside their instance,
any of their own backup/restore/upgrade operations could have silently
wiped it. A dedicated instance removes that risk entirely — nothing here
depends on Movu's database lifecycle in any way.

No schema isolation machinery needed anymore (no cross-schema safety
filters in migrations/env.py either) — this is genuinely our own database,
default "public" schema, full control.
"""

import uuid

from sqlalchemy import Column, String, DateTime, Integer, Boolean, JSON, func

from database import Base


class OrderMapping(Base):
    """
    One row per order the middleware has forwarded to Movu OPS. Maps the
    ShippingBo order back to whatever Movu created, and tracks a
    normalized state so we don't need to re-query Movu just to know
    "where is this order at."
    """
    __tablename__ = "order_mapping"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    xano_order_id = Column(String, nullable=False, index=True)
    movu_order_id = Column(String, nullable=False, index=True, unique=True)

    terminal_id = Column(String, nullable=True)
    gate_id = Column(String, nullable=True)

    current_state = Column(String, nullable=False, default="Created")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WebhookLog(Base):
    """
    Every inbound webhook (from Movu or from ShippingBo), keyed on a
    notification_id where available. Makes webhook handling idempotent.
    """
    __tablename__ = "webhook_log"

    id = Column(Integer, primary_key=True, autoincrement=True)

    notification_id = Column(String, nullable=False, unique=True, index=True)

    source = Column(String, nullable=False)  # "movu" | "shippingbo" | "shippingbo_preparation"
    notification_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=True)

    received_at = Column(DateTime(timezone=True), server_default=func.now())
    processed = Column(Boolean, default=False, nullable=False)


class RetryQueue(Base):
    """
    Failed outbound calls to Movu OPS (or ShippingBo) land here instead of
    just being logged and dropped.
    """
    __tablename__ = "retry_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)

    target_endpoint = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)

    attempt_count = Column(Integer, default=0, nullable=False)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)

    # pending | success | failed_permanent
    status = Column(String, default="pending", nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PreparationRun(Base):
    """
    One row per ShippingBo PreparationRun, created/updated from the
    /webhook/preparation notifications. This is the parent record the
    logistics interface lists — "what preparation runs exist, what state
    are they in." The actual SKU/emplacement/quantity detail is NOT in
    the webhook payload (confirmed from a real sample, Aug 26) — that
    comes from an uploaded PDF, parsed into PreparationRunPack rows below.

    Movu has no concept of "preparation run" at all — this table exists
    purely for the middleware's own interface, matching the confirmed
    design: Movu only ever receives individual pack-fetch requests, never
    anything about the run/session as a whole.
    """
    __tablename__ = "preparation_run"

    id = Column(String, primary_key=True)  # ShippingBo's own PreparationRun id, used directly as PK

    # Real state values confirmed from live webhook traffic (Aug 26):
    # packages_generated -> ps_generated -> (further states not yet observed)
    state = Column(String, nullable=False)
    package_count = Column(Integer, nullable=True)

    detail_uploaded = Column(Boolean, default=False, nullable=False)
    detail_filename = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PreparationRunPack(Base):
    """
    One row per SKU/emplacement/quantity line parsed from an uploaded
    preparation-run PDF. is_movu_stocked gets set by cross-checking the
    parsed emplacement against Movu's live handling units (GET
    /api/v3/handlingunits) at upload time — self-updating, no manually
    maintained whitelist needed.

    status tracks actual physical fulfillment per pack, driven by Movu's
    own webhook notifications once a mission is requested — "4 of 7 packs
    delivered" granularity, not a single flag on the whole run.
    """
    __tablename__ = "preparation_run_pack"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    preparation_run_id = Column(String, nullable=False, index=True)

    sku = Column(String, nullable=False)
    designation = Column(String, nullable=True)
    emplacement = Column(String, nullable=False)  # raw value as parsed from the PDF
    quantity = Column(Integer, nullable=False)

    # Set at upload time by checking `emplacement` against Movu's live
    # handling units. False/null emplacements (e.g. "RELOAD7"-style,
    # non-Movu stock) are kept in the table for visibility but excluded
    # from any Movu mission request.
    is_movu_stocked = Column(Boolean, nullable=True)
    movu_handling_unit_id = Column(String, nullable=True)
    movu_order_id = Column(String, nullable=True)  # correlates /webhook/movu notifications back to this row

    # Captured from the OrderLinePresented notification — needed to call
    # POST /api/v3/terminals/{terminalId}/gate/{gateId}/release once the
    # colleague confirms picking is done (sends the tote back to storage).
    presented_terminal_id = Column(String, nullable=True)
    presented_gate_id = Column(String, nullable=True)

    # pending | dry_run | sent | in_progress | presented | returning | completed | cancelled | failed
    status = Column(String, default="pending", nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    """
    Internal-only accounts for the logistics interface. NO self-service
    signup exists anywhere in this codebase, intentionally — accounts are
    only ever created by running create_user.py directly on VM101. This
    matches the requirement: internal tool, no one outside the team can
    ever create their own login.
    """
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)

    # Terminal identity, for accounts dedicated to a specific physical
    # station PC (e.g. "MPS1", "MPS2", "MPS3"). NULL for office/admin
    # accounts. Used by the preparation trigger and replenishment
    # actions instead of asking the colleague to select/scan terminal
    # every time — the logged-in account IS the terminal.
    terminal = Column(String, nullable=True)

    # Lets an account be disabled without deleting it (keeps history/audit
    # trail intact if someone leaves the team).
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)


class InboundRequest(Base):
    """
    One row per scanned pack sent to Movu for storage ("mise en stock" /
    inbound flow). ShippingBo already knows independently which product
    went into this pack (their own scan process, separate system) — Movu
    doesn't need product identity, only the pack ID itself, per the
    confirmed "carrier ID" design (Movu resolves location, not content).
    """
    __tablename__ = "inbound_requests"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    handling_unit_id = Column(String, nullable=False, index=True)  # the scanned barcode
    gate = Column(String, nullable=True)  # which physical gate the colleague used

    movu_order_id = Column(String, nullable=True)
    # requested | dry_run | success | failed
    status = Column(String, default="requested", nullable=False)
    error_message = Column(String, nullable=True)

    requested_by_email = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ReplenishmentRequest(Base):
    """
    "Appeler un bac" — calls an EXISTING handling unit (already inside
    Movu's racks, empty or partially stocked — Movu doesn't distinguish)
    out to a gate so a colleague can add more product to it. Confirmed
    from Atome 3D.docx: "Replenishment of partially filled boxes...
    can be done by creating an out order, making the adjustments on WMS
    side and create a new in order with the adjusted parameters."

    So this table only tracks the OUT half (retrieval). Once the pack
    is presented, the colleague physically adds product, then uses the
    EXISTING, unchanged mise-en-stock scan flow to send it back via a
    fresh In order — reusing InboundRequest, not this table, for that
    second half.
    """
    __tablename__ = "replenishment_requests"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    handling_unit_id = Column(String, nullable=False, index=True)  # typed, not scanned — pack is inside the rack

    movu_order_id = Column(String, nullable=True)

    # Captured from the OrderLinePresented notification — tells the
    # colleague which gate to physically go to.
    presented_terminal_id = Column(String, nullable=True)
    presented_gate_id = Column(String, nullable=True)

    # requested | dry_run | sent | in_progress | presented | failed
    status = Column(String, default="requested", nullable=False)
    error_message = Column(String, nullable=True)

    requested_by_email = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
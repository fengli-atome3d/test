"""
ORM models for the middleware's own bookkeeping tables — NOT Movu's or
ShippingBo's data. These live in the `middleware` schema, isolated from
Movu Ops's own tables in the same `movu_ops` database (same Postgres
instance on VM100, separate schema — see DB decision #3 in the project log).

Nothing here stores product/SKU data as a source of truth. This is purely
the "translation ledger" between ShippingBo/Xano order IDs and Movu order/
handling-unit IDs, plus idempotency and retry bookkeeping.
"""

import uuid

from sqlalchemy import Column, String, DateTime, Integer, Boolean, JSON, func

from database import Base

SCHEMA = "middleware"


class OrderMapping(Base):
    """
    One row per order the middleware has forwarded to Movu OPS. Maps the
    Xano/ShippingBo order back to whatever Movu created, and tracks a
    normalized state so we don't need to re-query Movu just to know
    "where is this order at."
    """
    __tablename__ = "order_mapping"
    __table_args__ = {"schema": SCHEMA}

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    xano_order_id = Column(String, nullable=False, index=True)
    movu_order_id = Column(String, nullable=False, index=True, unique=True)

    terminal_id = Column(String, nullable=True)
    gate_id = Column(String, nullable=True)

    # Normalized against Movu's real order states (Created, Active,
    # Processed, Finished, Aborted, ...) — see Annex 16.1 in the functional
    # design doc for the full list.
    current_state = Column(String, nullable=False, default="Created")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WebhookLog(Base):
    """
    Every inbound webhook (from Movu or from Xano), keyed on Movu's own
    notification_id where available. This is what makes webhook handling
    idempotent — if the same notification arrives twice (retries on Movu's
    side, network blips), we can tell and skip reprocessing it.
    """
    __tablename__ = "webhook_log"
    __table_args__ = {"schema": SCHEMA}

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Movu's own notification UUID when the source is "movu". For Xano
    # webhooks (no equivalent field observed yet), generate one ourselves
    # so the uniqueness constraint still functions.
    notification_id = Column(String, nullable=False, unique=True, index=True)

    source = Column(String, nullable=False)  # "movu" | "xano"
    notification_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=True)

    received_at = Column(DateTime(timezone=True), server_default=func.now())
    processed = Column(Boolean, default=False, nullable=False)


class RetryQueue(Base):
    """
    Failed outbound calls to Movu OPS (or Xano, if we ever call back into
    it) land here instead of just being logged and dropped.
    """
    __tablename__ = "retry_queue"
    __table_args__ = {"schema": SCHEMA}

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
    __table_args__ = {"schema": SCHEMA}

    id = Column(String, primary_key=True)  # ShippingBo's own PreparationRun id, used directly as PK

    # Real state values confirmed from live webhook traffic (Aug 26):
    # packages_generated -> ps_generated -> (further states not yet observed)
    state = Column(String, nullable=False)
    package_count = Column(Integer, nullable=True)

    pdf_uploaded = Column(Boolean, default=False, nullable=False)
    pdf_filename = Column(String, nullable=True)

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
    maintained whitelist needed (replaces the earlier
    MOVU_STOCKED_PRODUCT_REFS idea for this flow specifically).

    status tracks the actual physical fulfillment per pack, driven by
    Movu's own webhook notifications once a mission is requested — this
    is the "4 of 7 packs delivered" granularity discussed, not a single
    flag on the whole run.
    """
    __tablename__ = "preparation_run_pack"
    __table_args__ = {"schema": SCHEMA}

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

    # pending | mission_requested | delivered | ignored_not_movu
    status = Column(String, default="pending", nullable=False)

    mission_requested_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
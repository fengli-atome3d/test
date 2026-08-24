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
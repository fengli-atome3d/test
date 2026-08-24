import os
from dotenv import load_dotenv

load_dotenv()

# --- ShippingBo settings ------------------------------------------------------
# Used if/when the middleware needs to call ShippingBo directly (e.g. to look
# up product/stock details, or to mark an order as shipped/updated after
# Movu OPS finishes a pick) rather than only receiving its webhook.
SHIPPINGBO_API_TOKEN = os.getenv("SHIPPINGBO_API_TOKEN", "")
SHIPPINGBO_API_USER = os.getenv("SHIPPINGBO_API_USER", "")
SHIPPINGBO_API_VERSION = os.getenv("SHIPPINGBO_API_VERSION", "1")

# --- Movu OPS (WES) settings -------------------------------------------------
# Base URL of the Movu OPS API.
# TODO CONFIRM: config.py says port 9001 (tested working), but README.md
# claimed port 5000 per appsettings.json. Verify against the real
# appsettings.json on VM100 and delete whichever note is wrong.
MOVU_OPS_BASE_URL = os.getenv("MOVU_OPS_BASE_URL", "https://192.168.1.18:9001")

# The Movu OPS test tool uses a self-signed cert (same reason its own
# WebhooksOptions.IgnoreSslErrors is set to true in appsettings.json).
# Keep this false until/unless VM100 gets a trusted certificate.
MOVU_OPS_VERIFY_SSL = os.getenv("MOVU_OPS_VERIFY_SSL", "false").lower() in ("1", "true", "yes")

# The MPS terminal (workstation) to send cycle/piece-picking orders to.
MOVU_TERMINAL_ID = os.getenv("MOVU_TERMINAL_ID", "MPS1")

# --- Trigger logic ------------------------------------------------------------
# Order "state" values (from Xano/ShippingBo) that mean "this order is ready
# to be picked / sent to the warehouse". Comma-separated if there are several.
#
# Confirmed from real Xano data: "to_be_prepared" is the state to act on.
# ("waiting_for_payment" is an earlier state and should NOT trigger a Movu order.)
TRIGGER_STATES = set(
    s.strip() for s in os.getenv("TRIGGER_STATES", "to_be_prepared").split(",") if s.strip()
)

# --- Safety switch ------------------------------------------------------------
# While DRY_RUN is true, the middleware logs/returns the Movu payload it
# *would* send instead of actually POSTing it. Flip to false once the Movu
# OPS endpoint and the handling-unit lookup are both ready.
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")

# --- Middleware's own database (dedicated schema inside Movu's Postgres) -----
# Points at the `middleware` schema inside the `movu_ops` database on VM100.
# The middleware_app role has USAGE/CREATE on that schema only — no access to
# Movu's own tables. search_path is set at the role level (see DB setup docs),
# so queries don't need to prefix "middleware." explicitly, but the schema is
# still set explicitly below for clarity/safety.
MIDDLEWARE_DB_URL = os.getenv(
    "MIDDLEWARE_DB_URL",
    "postgresql+psycopg2://middleware_app:CHANGE_ME@192.168.1.18:5432/movu_ops",
)
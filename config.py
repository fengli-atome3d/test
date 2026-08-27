import os
from dotenv import load_dotenv

load_dotenv()

# --- ShippingBo settings ------------------------------------------------------
# Used when the middleware needs to call ShippingBo directly (currently: to
# sync inbound stock into the aggregate "MOVU" emplacement — see
# shippingbo_client.py, not yet implemented pending ShippingBo API details).
SHIPPINGBO_API_TOKEN = os.getenv("SHIPPINGBO_API_TOKEN", "")
SHIPPINGBO_API_USER = os.getenv("SHIPPINGBO_API_USER", "")
SHIPPINGBO_API_VERSION = os.getenv("SHIPPINGBO_API_VERSION", "1")

# --- ShippingBo webhook authentication -----------------------------------------
# ShippingBo's "Header libre" auth scheme: a shared-secret value sent as a
# custom header on every webhook call. Satisfies the boss's security
# requirement #1 (webhook authentication) — configured on ShippingBo's side
# as a JSON body like {"X-Webhook-Secret": "..."} on the webhook itself.
# Empty by default is intentionally FAIL-CLOSED: if this isn't set, every
# incoming webhook gets rejected (see main.py), rather than silently
# accepting unauthenticated requests.
SHIPPINGBO_WEBHOOK_HEADER_NAME = os.getenv("SHIPPINGBO_WEBHOOK_HEADER_NAME", "X-Webhook-Secret")
SHIPPINGBO_WEBHOOK_HEADER_VALUE = os.getenv("SHIPPINGBO_WEBHOOK_HEADER_VALUE", "")

# --- Movu webhook authentication -----------------------------------------------
# Same shared-secret-header pattern, mirrored for the Movu -> middleware
# direction. Registered with Movu via register_movu_webhook.py, using the
# httpHeaders field on POST /api/v3/webhooks/registrations. Fail-closed,
# same reasoning as the ShippingBo one above.
MOVU_WEBHOOK_HEADER_NAME = os.getenv("MOVU_WEBHOOK_HEADER_NAME", "X-Movu-Webhook-Secret")
MOVU_WEBHOOK_HEADER_VALUE = os.getenv("MOVU_WEBHOOK_HEADER_VALUE", "")

# The single aggregate emplacement in ShippingBo representing "everything
# currently stored in Movu" — chosen over per-bin emplacements since Movu's
# internal bin positions shift constantly and aren't meaningful to
# ShippingBo. May need to be created manually in ShippingBo's UI first,
# depending on whether their API can create emplacements on the fly
# (unconfirmed).
MOVU_STOCK_EMPLACEMENT_NAME = os.getenv("MOVU_STOCK_EMPLACEMENT_NAME", "MOVU")

# --- Movu OPS (WES) settings -------------------------------------------------
# Base URL of the Movu OPS API.
# TODO CONFIRM: config.py says port 9001 (tested working), but README.md
# claimed port 5000 per appsettings.json. Verify against the real
# appsettings.json on VM100 and delete whichever note is wrong.
MOVU_OPS_BASE_URL = os.getenv("MOVU_OPS_BASE_URL", "https://192.168.1.18:9001")

# Confirmed from swagger.json's securitySchemes: static token sent as an
# "x-api-key" header on every call. Empty by default — no key issued by
# Movu yet, even though this dev instance hasn't been enforcing it so far
# (every call worked without one). Add it once Sam provides an actual key.
MOVU_OPS_API_KEY = os.getenv("MOVU_OPS_API_KEY", "")

# The Movu OPS test tool uses a self-signed cert (same reason its own
# WebhooksOptions.IgnoreSslErrors is set to true in appsettings.json).
# Keep this false until/unless VM100 gets a trusted certificate.
MOVU_OPS_VERIFY_SSL = os.getenv("MOVU_OPS_VERIFY_SSL", "false").lower() in ("1", "true", "yes")

# The MPS terminal (workstation) to send cycle/piece-picking orders to.
MOVU_TERMINAL_ID = os.getenv("MOVU_TERMINAL_ID", "MPS1")

# --- Trigger logic ------------------------------------------------------------
# Order "state" values (from ShippingBo directly) that mean "this order is
# ready to be picked / sent to the warehouse".
#
# CONFIRMED from live production traffic (Aug 25, order 169530348): a real,
# non-duplicate transition waiting_for_stock -> to_be_prepared correctly
# matched and built a valid Movu Cycle order. "to_be_prepared" is the real
# ShippingBo state name, not just an assumption carried over from Xano.
TRIGGER_STATES = set(
    s.strip() for s in os.getenv("TRIGGER_STATES", "to_be_prepared").split(",") if s.strip()
)

# --- Movu stock scope ----------------------------------------------------------
# Which product_ref values are actually stored in the Escala shuttle
# warehouse (small items: filaments, resins) vs fulfilled separately
# (large items: 3D printers). Not yet defined in ShippingBo itself — see
# mapping.py. Empty by default on purpose: this is currently FAIL-OPEN
# (everything gets forwarded to Movu when the list is empty) — safe today
# only because DRY_RUN is also true. MUST be populated (or replaced with a
# real ShippingBo-side field) before DRY_RUN is ever turned off.
MOVU_STOCKED_PRODUCT_REFS = set(
    s.strip() for s in os.getenv("MOVU_STOCKED_PRODUCT_REFS", "").split(",") if s.strip()
)

# --- Safety switch ------------------------------------------------------------
# While DRY_RUN is true, the middleware logs/returns the Movu payload it
# *would* send instead of actually POSTing it. Flip to false only once:
#   1. MOVU_STOCKED_PRODUCT_REFS is populated (or replaced by a real check)
#   2. TRIGGER_STATES reflects ShippingBo's real state name, not the Xano one
#   3. shippingbo_client.update_movu_stock() is actually implemented
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")

# --- Stale inbound mission detection ------------------------------------
# Used by the Prometheus custom gauge (stale_inbound_requests_total) in
# main.py. Threshold matches the Grafana SQL alert built the same day
# ("Inbound mission stuck") — two independent detection mechanisms for
# the same real gap (Movu doesn't reliably notify on physical failures,
# confirmed from the obstructed-path incident weeks ago).
STALE_INBOUND_THRESHOLD_MINUTES = int(os.getenv("STALE_INBOUND_THRESHOLD_MINUTES", "30"))

# --- Middleware's own database (dedicated Postgres container on VM101) -----
# As of Aug 26: fully dedicated Postgres container (docker-compose
# "postgres" service), NOT shared with Movu's instance anymore. Resolves
# the risk flagged after Sam's email — no coupling to Movu's DB lifecycle.
MIDDLEWARE_DB_URL = os.getenv(
    "MIDDLEWARE_DB_URL",
    "postgresql+psycopg2://middleware_app:CHANGE_ME@postgres:5432/middleware",
)

# --- Internal logistics interface auth ------------------------------------
# Signs session cookies for the internal picking interface. No signup
# flow exists anywhere — accounts only created via create_user.py on
# VM101. Generate with `openssl rand -base64 32`.
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "")
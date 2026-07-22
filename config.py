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
# Base URL of the Movu OPS API. Confirmed on VM100: the app runs on port 9001
# over HTTPS with a self-signed certificate (browser shows "Not secure").
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
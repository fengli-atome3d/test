"""
Run this ONCE (or again if the registration ever needs updating) to tell
Movu OPS to send its order lifecycle notifications to our /webhook/movu
endpoint, with the shared-secret header included on every call.

Usage:
    python register_movu_webhook.py

Confirmed from swagger.json:
    POST /api/v3/webhooks/registrations
    body: WebhookRegistrationDetails { address, contextTypes, version, httpHeaders, webhookFilters }
"""

import httpx
import config

REGISTRATION_URL = f"{config.MOVU_OPS_BASE_URL}/api/v3/webhooks/registrations"

payload = {
    "address": "https://movu.izylog.com/webhook/movu",
    # "Order" covers order/orderline lifecycle events (OrderCreated,
    # OrderFinished, OrderLineProcessed, etc). "Load" covers handling-unit
    # events (HandlingUnitStored, HandlingUnitDiscarded) — needed for the
    # inbound-stock-sync logic in main.py. Confirmed as valid contextType
    # values from real notification samples seen in the functional design doc.
    "contextTypes": ["Order", "Load"],
    "version": "V1",
    "httpHeaders": {
        config.MOVU_WEBHOOK_HEADER_NAME: config.MOVU_WEBHOOK_HEADER_VALUE,
    },
}

if __name__ == "__main__":
    if not config.MOVU_WEBHOOK_HEADER_VALUE:
        raise SystemExit(
            "MOVU_WEBHOOK_HEADER_VALUE is not set in .env — generate one "
            "(e.g. `openssl rand -base64 32`) and set it before registering."
        )

    print(f"Registering webhook: {payload['address']}")
    print(f"Context types: {payload['contextTypes']}")

    resp = httpx.post(
        REGISTRATION_URL,
        json=payload,
        verify=config.MOVU_OPS_VERIFY_SSL,
        timeout=10,
    )

    print(f"Status: {resp.status_code}")
    print(resp.text)

    if resp.status_code == 200:
        print("\nRegistered successfully. Save the registrationId above if you "
              "need to edit/remove this registration later (PUT/DELETE "
              "/api/v3/webhooks/registrations/{id}).")

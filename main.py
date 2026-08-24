import logging

import httpx
from fastapi import FastAPI, Request, HTTPException
from pydantic import ValidationError

import config
from models import XanoOrderWebhook
from mapping import build_movu_order

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("atome_middleware")

app = FastAPI(title="Atome3D Order Middleware")


@app.get("/health")
def health():
    return {"status": "ok", "dry_run": config.DRY_RUN, "trigger_states": list(config.TRIGGER_STATES)}


@app.post("/webhook/order")
async def receive_order_webhook(request: Request):
    raw_body = await request.json()
    logger.info("Received order webhook: id=%s state=%s", raw_body.get("id"), raw_body.get("state"))

    try:
        order = XanoOrderWebhook.model_validate(raw_body)
    except ValidationError as e:
        logger.error("Payload failed validation: %s", e)
        raise HTTPException(status_code=422, detail=e.errors())

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
            "Order %s produced zero orderDemands (no items had a product_ref) — nothing to send.",
            order.id,
        )

    if config.DRY_RUN:
        logger.info("[DRY_RUN] Would POST to Movu OPS: %s", movu_payload)
        return {"status": "dry_run", "movu_payload": movu_payload}

    url = f"{config.MOVU_OPS_BASE_URL}/api/v3/orders"
    async with httpx.AsyncClient(timeout=10, verify=config.MOVU_OPS_VERIFY_SSL) as client:
        try:
            resp = await client.post(url, json=movu_payload)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("Failed to forward order to Movu OPS: %s", e)
            raise HTTPException(status_code=502, detail=f"Movu OPS error: {e}")

    return {"status": "forwarded", "movu_response": resp.json() if resp.content else None}
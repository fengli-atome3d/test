import logging
from datetime import datetime, timedelta, timezone

from models import XanoOrderWebhook
import config

logger = logging.getLogger("atome_middleware")


def build_movu_order(order: XanoOrderWebhook) -> dict:
    """
    Transform a Xano/ShippingBo order into a Movu OPS 'Cycle' order using
    orderDemands (stockId + quantity) rather than an explicit handlingUnitId.

    Confirmed from movu_ops_schema.sql:
      - order_demand table: stock_id, quantity, gate, slot, state
      - order_line.order_demand_id links a resolved line back to its demand
      - carrier.storage_profile_stock_id + carrier.handling_unit_id is what
        Movu OPS searches internally to satisfy a demand
    So sending {"stockId": ..., "quantity": ...} lets Movu OPS do its own
    stock-to-bin resolution — we no longer need to know or guess a
    handlingUnitId ourselves.

    NOTE: this exact combination (type "Cycle" + orderDemands) has NOT been
    confirmed working against a live Movu OPS instance yet — Swagger's own
    examples only pair orderDemands with type "Out". This needs a real test.
    """
    movu_order_id = order.origin_ref or f"ORDER-{order.id}"

    # "order"."due" is NOT NULL in the schema; sending an explicit value
    # rather than null, even though null was accepted previously (the app
    # layer likely defaults it, but this is more predictable).
    due = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()

    order_demands = []
    for item in order.order_items:
        if not item.product_ref:
            logger.warning(
                "Order %s item %s has no product_ref — skipping, can't build a demand without a stockId.",
                order.id, item.id,
            )
            continue

        order_demands.append(
            {
                "id": f"{movu_order_id}-DEMAND-{item.id}",
                "stockId": item.product_ref,
                "quantity": item.quantity,
                "gate": None,
                "slot": None,
            }
        )

    movu_payload = {
        "id": movu_order_id,
        "type": "Cycle",
        "due": due,
        "priority": None,
        # Per Atome guidelines, cycle orders should be released=false so
        # bins go to staging first (reduces travel time during picking).
        "released": False,
        "terminal": config.MOVU_TERMINAL_ID,
        "orderDemands": order_demands,
        "orderLines": [],
    }

    return {
        "movu_payload": movu_payload,
        "unresolved_items": [],  # kept for API compatibility with main.py; always empty now
    }
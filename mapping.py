import logging
from datetime import datetime, timedelta, timezone

from models import OrderObject
import config

logger = logging.getLogger("atome_middleware")


def build_movu_order(order: OrderObject) -> dict:
    """
    Transform a ShippingBo order into a Movu OPS 'Cycle' order using
    orderDemands (stockId + quantity) rather than an explicit handlingUnitId.

    Confirmed from movu_ops_schema.sql:
      - order_demand table: stock_id, quantity, gate, slot, state
      - order_line.order_demand_id links a resolved line back to its demand
      - carrier.storage_profile_stock_id + carrier.handling_unit_id is what
        Movu OPS searches internally to satisfy a demand
    So sending {"stockId": ..., "quantity": ...} lets Movu OPS do its own
    stock-to-bin resolution — we no longer need to know or guess a
    handlingUnitId ourselves.

    IMPORTANT — not every order item belongs in Movu at all. Atome3D sells
    both small stock (filaments, resins — physically stored in the Escala
    shuttle warehouse) and large items (3D printers — never stocked in
    Movu, fulfilled separately). ShippingBo has no field distinguishing
    this yet (stock_type_ref is currently unused/null on every item), so
    until that's defined (see config.MOVU_STOCKED_PRODUCT_REFS), any item
    NOT on the whitelist is skipped rather than blindly sent to Movu as a
    demand it can never satisfy.
    """
    movu_order_id = order.origin_ref or f"ORDER-{order.id}"

    # "order"."due" is NOT NULL in the schema; sending an explicit value
    # rather than null, even though null was accepted previously (the app
    # layer likely defaults it, but this is more predictable).
    due = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()

    order_demands = []
    skipped_items = []

    for item in order.order_items:
        if not item.product_ref:
            logger.warning(
                "Order %s item %s has no product_ref — skipping, can't build a demand without a stockId.",
                order.id, item.id,
            )
            skipped_items.append({"item_id": item.id, "reason": "no_product_ref"})
            continue

        # Guardrail: only forward items we know are actually stored in Movu.
        # config.MOVU_STOCKED_PRODUCT_REFS is empty by default on purpose —
        # nothing gets sent to Movu until this whitelist (or its eventual
        # replacement, e.g. a real ShippingBo stock_type field) is populated.
        if config.MOVU_STOCKED_PRODUCT_REFS and item.product_ref not in config.MOVU_STOCKED_PRODUCT_REFS:
            logger.info(
                "Order %s item %s (product_ref=%s) is not in MOVU_STOCKED_PRODUCT_REFS — "
                "treating as externally-fulfilled (e.g. large item), not sending to Movu.",
                order.id, item.id, item.product_ref,
            )
            skipped_items.append({"item_id": item.id, "product_ref": item.product_ref, "reason": "not_movu_stocked"})
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
        # bins go to staging first. Also matches the real operational flow:
        # a logistics colleague manually triggers picking/release at the
        # terminal — the middleware does not auto-execute anything.
        "released": False,
        "terminal": config.MOVU_TERMINAL_ID,
        "orderDemands": order_demands,
        "orderLines": [],
    }

    if not order_demands and order.order_items:
        logger.warning(
            "Order %s produced ZERO orderDemands out of %d item(s) — "
            "either none had a product_ref, or none are in MOVU_STOCKED_PRODUCT_REFS "
            "(which is empty by default until the whitelist is defined).",
            order.id, len(order.order_items),
        )

    return {
        "movu_payload": movu_payload,
        "skipped_items": skipped_items,
    }
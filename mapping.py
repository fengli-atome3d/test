import logging
from typing import Optional

from models import XanoOrderWebhook
import config

logger = logging.getLogger("atome_middleware")


def resolve_handling_unit(product_ref: str) -> Optional[str]:
    """
    *** THE MISSING LINK ***

    Movu OPS cycle/out orders operate on a `handlingUnitId` — the physical
    bin/tote in the Escala warehouse — not on a product reference or a
    quantity. The order payload from Xano/ShippingBo only tells us WHAT
    product and HOW MANY, not WHICH BIN it's currently stored in.

    We need a lookup here: product_ref -> handlingUnitId(s) that currently
    hold that product. That data has to come from somewhere — either:
      - a stock/location table already in Xano, or
      - Movu OPS's own stock/inventory query (if it exposes one), or
      - a separate WMS/stock service.

    Until that's wired up, this returns None so the caller can flag the line
    as unresolved instead of sending a broken request to Movu OPS.
    """
    # TODO: replace with a real lookup once the stock source is confirmed.
    return None


def build_movu_order(order: XanoOrderWebhook) -> dict:
    """
    Transform a Xano/ShippingBo order into a Movu OPS 'Cycle' order
    (piece picking), per the Atome 3D functional design doc section 9.
    """
    movu_order_id = order.origin_ref or f"ORDER-{order.id}"

    order_lines = []
    unresolved_items = []

    for item in order.order_items:
        handling_unit_id = (
            resolve_handling_unit(item.product_ref) if item.product_ref else None
        )

        if handling_unit_id is None:
            unresolved_items.append(item.model_dump())
            continue

        order_lines.append(
            {
                "id": f"{movu_order_id}-{item.id}",
                "gate": None,  # left blank on purpose — let Movu OPS pick the gate
                "slot": None,
                "handlingUnitId": handling_unit_id,
            }
        )

    movu_payload = {
        "id": movu_order_id,
        "type": "cycle",
        "due": None,
        "priority": None,
        # Per Atome guidelines, cycle orders should be released=false so
        # bins go to staging first (reduces travel time during picking).
        "released": False,
        "terminal": config.MOVU_TERMINAL_ID,
        "orderLines": order_lines,
    }

    return {
        "movu_payload": movu_payload,
        "unresolved_items": unresolved_items,
    }

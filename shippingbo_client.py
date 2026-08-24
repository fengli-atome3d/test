"""
Outbound calls TO ShippingBo. Currently stubbed — ShippingBo's actual API
for updating emplacement stock (endpoint, payload shape, whether it can
create a new emplacement or only update an existing one) is not yet
confirmed. Do NOT guess at this; fill in update_movu_stock() once the real
API contract is known.

config.SHIPPINGBO_API_TOKEN / SHIPPINGBO_API_USER already exist for this.
"""

import logging

import httpx

import config

logger = logging.getLogger("atome_middleware")


class ShippingBoAPIError(Exception):
    pass


def update_movu_stock(stock_id: str, quantity_delta: int) -> None:
    """
    Update ShippingBo's stock for the aggregate "MOVU" emplacement
    (config.MOVU_STOCK_EMPLACEMENT_NAME) — increments by quantity_delta
    for a positive value (inbound complete), would decrement for a
    negative one (not currently used — outbound/picking is handled
    natively by ShippingBo via the PDA scan, confirmed, no middleware
    involvement needed there).

    TODO: NOT IMPLEMENTED. Needs, from ShippingBo's own API docs:
      - The real endpoint (likely something like POST/PATCH
        /emplacements/{id}/stock or similar — unconfirmed)
      - Payload shape: absolute quantity vs a delta
      - Whether the "MOVU" emplacement must be pre-created manually in
        ShippingBo's UI first, or can be created via the API on first use
      - Auth: presumably config.SHIPPINGBO_API_TOKEN as a header — confirm
        the exact header name/scheme from their docs
    """
    raise NotImplementedError(
        "ShippingBo emplacement stock API not yet confirmed — see docstring. "
        f"Would have updated stock_id={stock_id} by {quantity_delta:+d} "
        f"on emplacement '{config.MOVU_STOCK_EMPLACEMENT_NAME}'."
    )

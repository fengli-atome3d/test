"""
Standalone test script (not part of the FastAPI app) to exercise the full
In -> Cycle flow directly against Movu OPS, with polling so we don't fire the
Cycle order before the handling unit actually exists in the warehouse.

Reads the same .env file the FastAPI app uses, so switching between your Mac
(Tailscale IP) and VM101 (LAN IP) just means switching .env, not this file.

Usage:
    python test_movu_flow.py
"""

import time
import uuid

import httpx

import config

BASE_URL = config.MOVU_OPS_BASE_URL
VERIFY_SSL = config.MOVU_OPS_VERIFY_SSL

# NOTE: MPS1 and MPS2 do NOT support inbound (per the layout doc) — only MPS3
# has overweight/overfill detection and is listed as supporting Inbound.
INBOUND_TERMINAL = "MPS3"
PICKING_TERMINAL = config.MOVU_TERMINAL_ID

STOCK_ID = "4008050043"  # real product_ref from the sample order
SUFFIX = uuid.uuid4().hex[:8]  # keep IDs unique across test runs


def post_order(payload: dict) -> dict:
    with httpx.Client(verify=VERIFY_SSL, timeout=10) as client:
        resp = client.post(f"{BASE_URL}/api/v3/orders", json=payload)
        print(f"POST /api/v3/orders -> {resp.status_code}")
        print(resp.text)
        resp.raise_for_status()
        return resp.json()


def get_order(order_id: str) -> dict:
    with httpx.Client(verify=VERIFY_SSL, timeout=10) as client:
        resp = client.get(f"{BASE_URL}/api/v3/orders/{order_id}")
        resp.raise_for_status()
        return resp.json()


def wait_for_state(order_id: str, target_states, timeout_s: int = 60, poll_every_s: int = 3):
    """Poll GET /api/v3/orders/{id} until order.state is one of target_states."""
    elapsed = 0
    while elapsed < timeout_s:
        order = get_order(order_id)
        state = order.get("state")
        print(f"  ... order {order_id} state = {state}")
        if state in target_states:
            return order
        time.sleep(poll_every_s)
        elapsed += poll_every_s
    raise TimeoutError(f"Order {order_id} did not reach {target_states} within {timeout_s}s (last seen state above)")


def run_in_order():
    order_id = f"ORDER-IN-TEST-{SUFFIX}"
    payload = {
        "id": order_id,
        "type": "In",
        "terminal": INBOUND_TERMINAL,
        "orderLines": [
            {
                "id": f"ORDERLINE-IN-{SUFFIX}",
                "HandlingUnitId": f"LOAD-TEST-{SUFFIX}",
                "Gate": f"{INBOUND_TERMINAL}G1",
                "Barcodes": [STOCK_ID],
                "storageProfile": {
                    "stockId": STOCK_ID,
                    "categories": ["B"],
                },
            }
        ],
    }
    print(f"\n=== Creating In order {order_id} on {INBOUND_TERMINAL} ===")
    post_order(payload)
    print("Waiting for it to reach Finished (handling unit stored)...")
    wait_for_state(order_id, target_states={"Finished"}, timeout_s=120)
    return f"LOAD-TEST-{SUFFIX}"


def run_cycle_order(handling_unit_id: str):
    order_id = f"ORDER-CYCLE-TEST-{SUFFIX}"
    payload = {
        "id": order_id,
        "type": "cycle",
        "due": None,
        "priority": None,
        "released": False,
        "terminal": PICKING_TERMINAL,
        "orderLines": [
            {
                "id": f"ORDERLINE-CYCLE-{SUFFIX}",
                "gate": None,
                "slot": None,
                "handlingUnitId": handling_unit_id,
            }
        ],
    }
    print(f"\n=== Creating Cycle order {order_id} on {PICKING_TERMINAL} ===")
    post_order(payload)
    print("Waiting for it to reach Processed (presented at workstation)...")
    wait_for_state(order_id, target_states={"Processed", "Finished"}, timeout_s=120)
    return order_id


if __name__ == "__main__":
    print(f"Using MOVU_OPS_BASE_URL={BASE_URL} (verify_ssl={VERIFY_SSL})")
    hu_id = run_in_order()
    run_cycle_order(hu_id)
    print("\nDone. Check the Ops UI Orders tab for full detail.")

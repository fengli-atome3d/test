# Atome3D Order Middleware

Receives order webhooks (Xano / ShippingBo) and forwards them to Movu OPS as
piece-picking ("cycle") orders.

## Setup on VM101

```bash
cd atome_middleware
python3 -m venv venv          # skip if you already have one
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # edit values as needed
```

## Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- Health check: `GET http://<vm101-ip>:8000/health`
- Webhook endpoint: `POST http://<vm101-ip>:8000/webhook/order`

## Test with the real sample payload

While `DRY_RUN=true` (default), the middleware won't call Movu OPS — it just
returns the payload it *would* send, so you can validate the mapping safely.

```bash
curl -X POST http://localhost:8000/webhook/order \
  -H "Content-Type: application/json" \
  -d @sample_order.json
```

`sample_order.json` now has `state: "to_be_prepared"` — confirmed as the
real state Xano uses when an order is ready for the warehouse. This will
trigger the build step and return something like:

```json
{
  "status": "dry_run",
  "movu_payload": {"id": "A3D-196020", "type": "cycle", "terminal": "MPS1", "orderLines": [], ...},
  "unresolved_items": [{"product_ref": "4008050043", "quantity": 1, ...}]
}
```

`orderLines` is empty and the item shows up in `unresolved_items` — that's
expected until gap #1 below is closed.

## Known gaps to close before going live

1. **`product_ref` → `handlingUnitId` lookup.** `mapping.py:resolve_handling_unit()`
   is a stub. Movu OPS needs to know which physical bin holds the requested
   product — that mapping has to come from a stock/location source we
   haven't identified yet.
2. **Point `MOVU_OPS_BASE_URL` at the real Movu OPS host** once known
   (per `appsettings.json`, it typically listens on port 5000).
3. **Expose this middleware's URL to Xano.** Once deployed, use VM101's
   reachable address (or a tunnel like `ngrok`/`cloudflared` for testing) as
   the webhook URL in the Xano trigger.

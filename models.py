from typing import Optional, List
from pydantic import BaseModel, ConfigDict


class OrderItem(BaseModel):
    # extra="ignore" so we don't break if ShippingBo/Xano adds new fields later
    model_config = ConfigDict(extra="ignore")

    id: int
    title: Optional[str] = None
    quantity: int
    product_ref: Optional[str] = None
    product_ean: Optional[str] = None


class Address(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company_name: Optional[str] = None
    street1: Optional[str] = None
    street2: Optional[str] = None
    zip: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None


class XanoOrderWebhook(BaseModel):
    """
    Shape based on the real sample payload from the Xano 'Order' webhook
    (state-change trigger, source: ShippingBo / Shopify).
    """
    model_config = ConfigDict(extra="ignore")

    id: int
    state: str
    origin: Optional[str] = None          # e.g. "Atome3D"
    origin_ref: Optional[str] = None       # e.g. "A3D-196020" — human-readable order ref
    source: Optional[str] = None           # e.g. "Shopify-1053"
    source_ref: Optional[str] = None
    order_items: List[OrderItem] = []
    shipping_address: Optional[Address] = None
    billing_address: Optional[Address] = None

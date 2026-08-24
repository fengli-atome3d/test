from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field


class OrderItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    title: Optional[str] = None
    quantity: int
    product_ref: Optional[str] = None
    product_ean: Optional[str] = None


class Address(BaseModel):
    """
    Real ShippingBo addresses carry far more fields than we modeled before
    (phone numbers, civility, company name, etc.). Only mapping what the
    middleware actually needs — extra="ignore" drops the rest safely.
    """
    model_config = ConfigDict(extra="ignore")

    company_name: Optional[str] = None
    street1: Optional[str] = None
    street2: Optional[str] = None
    zip: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    phone1: Optional[str] = None
    email: Optional[str] = None


class OrderTag(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    value: str


class OrderObject(BaseModel):
    """
    The actual order — nested under "object" in the real webhook payload,
    NOT at the top level like the old Xano-shaped model assumed.
    """
    model_config = ConfigDict(extra="ignore")

    id: int
    state: str
    origin: Optional[str] = None
    origin_ref: Optional[str] = None
    source: Optional[str] = None
    source_ref: Optional[str] = None
    custom_state: Optional[str] = None
    order_items: List[OrderItem] = []
    shipping_address: Optional[Address] = None
    billing_address: Optional[Address] = None
    order_tags: List[OrderTag] = []


class AdditionalData(BaseModel):
    """
    Describes the state transition that triggered this webhook — e.g.
    from "waiting_for_payment" to "waiting_for_stock". "from" is aliased
    since it's a reserved Python keyword.
    """
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    to: Optional[str] = None
    from_state: Optional[str] = Field(None, alias="from")
    field: Optional[str] = None


class ShippingBoOrderWebhook(BaseModel):
    """
    Real ShippingBo webhook payload for the order/status topic — confirmed
    from a live "waiting_for_payment" -> "waiting_for_stock" webhook
    (hook_id 103557). Replaces the old XanoOrderWebhook model, which was
    shaped around Xano's intermediary format, not ShippingBo's real one.

    NOTE: the correct trigger state (the one meaning "ready to send to
    the warehouse") is NOT yet confirmed — "waiting_for_stock" in the
    sample this was built from is a different, earlier transition. See
    config.py TRIGGER_STATES for the open question.
    """
    model_config = ConfigDict(extra="ignore")

    object: OrderObject
    hook_id: Optional[int] = None
    object_class: Optional[str] = None
    additional_data: Optional[AdditionalData] = None
    validation_time: Optional[str] = None


class ProductObject(BaseModel):
    """
    The actual product — nested under "object", same wrapper pattern as
    orders. Confirmed from a real product/stock webhook (hook_id 43642).

    IMPORTANT: "stock" and "total_physical_stock" disagreed in the sample
    this was built from (463 vs 0) with an empty "location" field — the
    exact relationship between these isn't understood yet. Don't assume
    they're interchangeable until confirmed.
    """
    model_config = ConfigDict(extra="ignore")

    id: int
    title: Optional[str] = None
    stock: Optional[int] = None
    total_physical_stock: Optional[int] = None
    location: Optional[str] = None
    user_ref: Optional[str] = None  # matches OrderItem.product_ref — confirmed
    source_ref: Optional[str] = None
    ean13: Optional[str] = None
    supplier: Optional[str] = None


class ProductAdditionalData(BaseModel):
    """
    Same from/to/field pattern as order webhooks, but the values here are
    stock counts (integers), not state name strings.
    """
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    to: Optional[int] = None
    from_value: Optional[int] = Field(None, alias="from")
    field: Optional[str] = None


class ShippingBoProductWebhook(BaseModel):
    """
    Real ShippingBo webhook payload for the product/stock topic (hook_id
    43642). NOT yet wired into any handler — purpose (e.g. driving
    MOVU_STOCKED_PRODUCT_REFS from the "location" field, vs. something
    else) needs confirming before building logic around it.
    """
    model_config = ConfigDict(extra="ignore")

    object: ProductObject
    hook_id: Optional[int] = None
    object_class: Optional[str] = None
    additional_data: Optional[ProductAdditionalData] = None
    validation_time: Optional[str] = None
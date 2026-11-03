"""The five tools the agent can call.

The language model only picks a tool and fills its arguments. Arguments are
validated with Pydantic, and every price, stock level and deadline is read
from the data or computed by ``emporio.rules``. Tools receive identifiers
(product_id, order_id), never business values, so a price typed or invented
by the model can never reach a calculation.
"""

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from emporio import rules
from emporio.data_loader import StoreData, normalize_email

TICKETS_DIR = Path(__file__).resolve().parent.parent / "tickets"
SEARCH_RESULT_LIMIT = 10

# Policy 5.2: acoustic drums, digital pianos and double basses may need a
# special shipping quote.
LARGE_ITEM_CATEGORIES = frozenset({"Baterias e Percussão", "Teclados e Pianos", "Cordas Orquestrais"})

# Singular forms, so "violao" finds "Violões" when typed in the free-text query.
CATEGORY_SEARCH_TERMS = {
    "Guitarras": "guitarra",
    "Baixos": "baixo contrabaixo",
    "Baterias e Percussão": "bateria percussao",
    "Teclados e Pianos": "teclado piano sintetizador",
    "Violões": "violao",
    "Instrumentos de Sopro (Madeiras)": "sopro",
    "Instrumentos de Sopro (Metais)": "sopro metal",
    "Cordas Orquestrais": "violino viola violoncelo",
    "Ukuleles": "ukulele",
}

CategoryName = Literal[
    "Guitarras",
    "Baixos",
    "Baterias e Percussão",
    "Teclados e Pianos",
    "Violões",
    "Instrumentos de Sopro (Madeiras)",
    "Instrumentos de Sopro (Metais)",
    "Cordas Orquestrais",
    "Ukuleles",
]

AVAILABILITY_ORDER = {"available": 0, "out_of_stock": 1, "coming_soon": 2, "discontinued": 3}

PAYMENT_LABELS = {
    "pix": "PIX",
    "debit": "cartão de débito",
    "boleto": "boleto",
    "credit": "cartão de crédito",
}

# Same message whether the order is missing or the e-mail is wrong, so the
# tool never reveals that an order number exists (policy 9).
ORDER_NOT_FOUND = "Pedido não encontrado. Confira o número do pedido e o e-mail usado na compra."


@dataclass(frozen=True)
class ToolContext:
    """What the code, not the model, provides to every tool call."""

    data: StoreData
    now: datetime
    tickets_dir: Path = TICKETS_DIR


class ToolArgs(BaseModel):
    """Base for tool arguments: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


class SearchProductsArgs(ToolArgs):
    query: str | None = Field(
        default=None,
        description="Words from the product name, model or brand, e.g. 'takamine gd20'. "
        "Use category for the instrument type.",
    )
    category: CategoryName | None = Field(default=None, description="Instrument category.")
    brand: str | None = Field(default=None, description="Brand name, e.g. 'Yamaha'.")
    max_price: float | None = Field(
        default=None, gt=0, description="Maximum current price in BRL (promotional price if any)."
    )


class GetOrderStatusArgs(ToolArgs):
    order_id: int = Field(gt=0, description="Order number given by the customer.")
    email: str = Field(min_length=3, max_length=254, description="E-mail used in the purchase.")


class ListActivePromotionsArgs(ToolArgs):
    pass


class GetPriceQuoteArgs(ToolArgs):
    product_id: int = Field(gt=0, description="product_id returned by search_products.")
    quantity: int = Field(default=1, ge=1, le=20, description="Number of units.")
    city: str | None = Field(default=None, description="Delivery city, if the customer said it.")


class CreateSupportTicketArgs(ToolArgs):
    ticket_type: Literal["complaint", "return_or_exchange", "data_deletion", "policy_question"] = Field(
        description="complaint; return_or_exchange request; data_deletion (LGPD); "
        "policy_question the policy does not cover."
    )
    summary: str = Field(
        min_length=10, max_length=1000, description="What the customer needs, in Portuguese."
    )
    order_id: int | None = Field(default=None, gt=0, description="Related order, if any.")
    contact_email: str | None = Field(
        default=None, max_length=254, description="E-mail for the team to reply."
    )


def search_products(args: SearchProductsArgs, context: ToolContext) -> dict[str, Any]:
    """Search the catalog, ignoring accents and case.

    Args:
        args: Validated search filters.
        context: Store data and current time.

    Returns:
        How many products matched and up to 10 of them, available ones first
        and cheapest first.
    """
    products = _priced_products(context.data)
    if args.query:
        search_text = (
            products["name"] + " " + products["brand"].fillna("") + " "
            + products["category"].map(CATEGORY_SEARCH_TERMS).fillna("")
        ).map(normalize_text)
        for token in normalize_text(args.query).split():
            matches = search_text.str.contains(token, regex=False)
            products, search_text = products[matches], search_text[matches]
    if args.category:
        products = products[products["category"] == args.category]
    if args.brand:
        products = products[products["brand"].fillna("").map(normalize_text) == normalize_text(args.brand)]
    if args.max_price:
        products = products[products["current_price"] <= args.max_price]

    products = products.assign(
        availability_rank=products["availability"].map(AVAILABILITY_ORDER)
    ).sort_values(["availability_rank", "current_price"])
    shown = products.head(SEARCH_RESULT_LIMIT)
    return {
        "total_found": len(products),
        "shown": len(shown),
        "products": [_product_summary(row) for row in shown.to_dict("records")],
    }


def get_order_status(args: GetOrderStatusArgs, context: ToolContext) -> dict[str, Any]:
    """Return an order only if the order number and e-mail match (policy 9).

    Args:
        args: Order number and e-mail typed by the customer.
        context: Store data and current time.

    Returns:
        Status, dates, items, tracking code and, for delivered orders, the
        return and exchange deadlines already computed. No phone or address.
    """
    data = context.data
    order_rows = data.orders[data.orders["order_id"] == args.order_id]
    if order_rows.empty:
        return {"found": False, "message": ORDER_NOT_FOUND}
    order = order_rows.iloc[0]
    customer = data.customers[data.customers["customer_id"] == order["customer_id"]].iloc[0]
    if customer["email"] != normalize_email(args.email):
        return {"found": False, "message": ORDER_NOT_FOUND}

    items = data.order_items[data.order_items["order_id"] == args.order_id].merge(
        data.products[["product_id", "name"]], on="product_id"
    )
    delivered_on = order["estimated_delivery"] if order["status"] == "delivered" else pd.NaT
    windows = None
    if pd.notna(delivered_on):
        windows = rules.return_windows(
            order["order_date"].date(), delivered_on.date(), _today(context)
        )
    return {
        "found": True,
        "order_id": int(order["order_id"]),
        "customer_first_name": customer["name"].split()[0],
        "status": order["status"],
        # Assumption: every order in the data is an online purchase (README).
        "purchase_channel": "online",
        "order_date": _iso_date(order["order_date"]),
        "estimated_delivery": _iso_date(order["estimated_delivery"]),
        "delivered_on": _iso_date(delivered_on),
        "payment": describe_payment(order["payment_method"]),
        "total_brl": float(order["total_brl"]),
        "items": [
            {"name": row["name"], "quantity": int(row["quantity"])}
            for row in items.to_dict("records")
        ],
        "tracking_code": order["tracking_code"] if pd.notna(order["tracking_code"]) else None,
        "notes": order["notes"] if pd.notna(order["notes"]) else None,
        "return_windows": _windows_summary(windows) if windows else None,
    }


def list_active_promotions(args: ListActivePromotionsArgs, context: ToolContext) -> dict[str, Any]:
    """List the promotions active in the data (policy 6 and 7.3).

    Args:
        args: No arguments.
        context: Store data and current time.

    Returns:
        Products on promotion, largest discount first, with original price,
        promotional price and discount.
    """
    products = _priced_products(context.data)
    on_promotion = products[products["discount_percent"].notna()].sort_values(
        "discount_percent", ascending=False
    )
    return {"promotions": [_product_summary(row) for row in on_promotion.to_dict("records")]}


def get_price_quote(args: GetPriceQuoteArgs, context: ToolContext) -> dict[str, Any]:
    """Quote a product with every payment option and shipping (policy 3, 5 and 6.2).

    Args:
        args: Product, quantity and optional delivery city.
        context: Store data and current time.

    Returns:
        Prices per payment method, valid installment plans, whether payment
        methods can be combined, and shipping. Totals include the shipping
        fee only when it is known (Campo Grande).
    """
    products = _priced_products(context.data)
    rows = products[products["product_id"] == args.product_id]
    if rows.empty:
        return {"error": f"Produto {args.product_id} não encontrado."}
    product = rows.iloc[0]

    on_promotion = pd.notna(product["discount_percent"])
    goods_total = rules.round_money(float(product["current_price"]) * args.quantity)
    shipping = _shipping_quote(goods_total, product["category"], args.city)
    shipping_fee = shipping.get("fee") or 0.0
    total = rules.round_money(goods_total + shipping_fee)
    quote = {
        "product": _product_summary(product.to_dict()),
        "quantity": args.quantity,
        "enough_stock": bool(product["stock_quantity"] >= args.quantity),
        "goods_total": goods_total,
        "shipping": shipping,
        "shipping_included_in_totals": shipping.get("fee") is not None,
        "debit_or_boleto_total": total,
        "pix_total": None,
        "pix_note": None,
        "credit_card_installments": [
            {"count": option.count, "value": option.value}
            for option in rules.installment_options(total)
        ],
        "can_combine_payment_methods": rules.can_combine_payments(total),
    }
    if on_promotion:
        quote["pix_note"] = "O desconto do PIX não se aplica sobre preço promocional (política 6.2)."
    else:
        quote["pix_total"] = rules.round_money(rules.pix_price(goods_total) + shipping_fee)
    return quote


def create_support_ticket(args: CreateSupportTicketArgs, context: ToolContext) -> dict[str, Any]:
    """Record a case for the human team in a local file outside Git.

    Args:
        args: Ticket type, summary and optional order and contact.
        context: Store data, current time and tickets folder.

    Returns:
        Protocol number, response deadline and when the team is back if the
        store is closed now.
    """
    context.tickets_dir.mkdir(parents=True, exist_ok=True)
    tickets_file = context.tickets_dir / "tickets.jsonl"
    existing = tickets_file.read_text(encoding="utf-8").splitlines() if tickets_file.exists() else []
    protocol = f"EM-{context.now:%Y%m%d}-{len(existing) + 1:04d}"
    deadline = rules.add_business_hours(context.now, rules.TICKET_RESPONSE_BUSINESS_HOURS)
    record = {
        "protocol": protocol,
        "created_at": context.now.isoformat(timespec="seconds"),
        "response_deadline": deadline.isoformat(timespec="minutes"),
        **args.model_dump(),
    }
    with tickets_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")

    store_open = rules.is_store_open(context.now)
    return {
        "protocol": protocol,
        "response_deadline": deadline.isoformat(timespec="minutes"),
        "store_open_now": store_open,
        "team_back_at": None if store_open else rules.next_opening(context.now).isoformat(timespec="minutes"),
    }


@dataclass(frozen=True)
class Tool:
    """A tool the model can call: name, description, argument schema and code."""

    name: str
    description: str
    args_model: type[ToolArgs]
    run: Callable[[Any, ToolContext], dict[str, Any]]


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            "search_products",
            "Search the instrument catalog. Returns price, active promotion, stock and availability.",
            SearchProductsArgs,
            search_products,
        ),
        Tool(
            "get_order_status",
            "Get an order's status, items, tracking code and return/exchange deadlines. "
            "Requires the order number and the purchase e-mail.",
            GetOrderStatusArgs,
            get_order_status,
        ),
        Tool(
            "list_active_promotions",
            "List the promotions active today.",
            ListActivePromotionsArgs,
            list_active_promotions,
        ),
        Tool(
            "get_price_quote",
            "Quote a product: PIX, debit, boleto and credit card installments, and shipping.",
            GetPriceQuoteArgs,
            get_price_quote,
        ),
        Tool(
            "create_support_ticket",
            "Open a ticket for the human team: complaints, return or exchange requests, "
            "data deletion requests, or questions the policy does not cover.",
            CreateSupportTicketArgs,
            create_support_ticket,
        ),
    )
}


def execute_tool(name: str, arguments: str, context: ToolContext) -> dict[str, Any]:
    """Validate the model's arguments and run a tool.

    Errors are returned as data instead of raised, so the model can read
    them and correct its call.

    Args:
        name: Tool name chosen by the model.
        arguments: JSON string with the arguments.
        context: Store data and current time.

    Returns:
        The tool result, or a dict with an ``error`` key.
    """
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        args = tool.args_model.model_validate_json(arguments or "{}")
    except ValidationError as error:
        return {
            "error": "Invalid arguments",
            "details": error.errors(include_url=False, include_context=False),
        }
    return tool.run(args, context)


def normalize_text(text: str) -> str:
    """Lowercase a text and remove accents, so "Violão" matches "violao".

    Args:
        text: Any text.

    Returns:
        The normalized text.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold().strip()


def describe_payment(raw_method: str) -> str:
    """Turn a payment code like "credit_6x" into Portuguese text.

    Args:
        raw_method: Payment code from orders.csv.

    Returns:
        A description such as "cartão de crédito em 6x".
    """
    method, _, installments = raw_method.partition("_")
    label = PAYMENT_LABELS.get(method, raw_method)
    return f"{label} em {installments}" if installments else label


def is_local_city(city: str) -> bool:
    """Check whether a delivery city is Campo Grande (policy 5.1).

    Args:
        city: City as typed, e.g. "Campo Grande - MS".

    Returns:
        True for Campo Grande, with or without the state.
    """
    words = re.sub(r"[^a-z ]", " ", normalize_text(city)).split()
    return [word for word in words if word != "ms"] == ["campo", "grande"]


def _priced_products(data: StoreData) -> pd.DataFrame:
    active = data.promotions.loc[
        data.promotions["is_active"], ["product_id", "discount_percent", "description"]
    ].rename(columns={"description": "promotion_name"})
    products = data.products.merge(active, on="product_id", how="left")
    products["current_price"] = [
        rules.promotional_price(price, int(discount)) if pd.notna(discount) else price
        for price, discount in zip(products["price_brl"], products["discount_percent"])
    ]
    products["availability"] = [
        _availability(status, stock)
        for status, stock in zip(products["status"], products["stock_quantity"])
    ]
    return products


def _availability(status: str, stock: int) -> str:
    if status in {"discontinued", "coming_soon"}:
        return status
    return "available" if stock > 0 else "out_of_stock"


def _product_summary(row: dict[str, Any]) -> dict[str, Any]:
    promotion = None
    if pd.notna(row["discount_percent"]):
        promotion = {
            "name": row["promotion_name"],
            "discount_percent": int(row["discount_percent"]),
            "promotional_price": float(row["current_price"]),
        }
    return {
        "product_id": int(row["product_id"]),
        "name": row["name"],
        "brand": row["brand"] if pd.notna(row["brand"]) else None,
        "category": row["category"],
        "description": row["description"],
        "list_price": float(row["price_brl"]),
        "promotion": promotion,
        "stock_quantity": int(row["stock_quantity"]),
        "availability": row["availability"],
    }


def _shipping_quote(goods_total: float, category: str, city: str | None) -> dict[str, Any]:
    if not city:
        return {"city": None, "fee": None, "note": "Cidade não informada: frete não calculado."}
    if is_local_city(city):
        return {
            "city": "Campo Grande",
            "method": "motoboy próprio",
            "fee": rules.local_shipping_fee(goods_total),
            "delivery_time": rules.LOCAL_DELIVERY_DAYS,
        }
    return {
        "city": city,
        "fee": None,
        "options": list(rules.CARRIERS),
        "note": "O valor do frete depende do CEP, peso e dimensões, com seguro e rastreio incluídos.",
        "special_quote_may_be_required": category in LARGE_ITEM_CATEGORIES,
    }


def _windows_summary(windows: rules.ReturnWindows) -> dict[str, Any]:
    def deadline(value: rules.Deadline) -> dict[str, Any]:
        return {"last_day": value.last_day.isoformat(), "is_open": value.is_open}

    return {
        "days_since_purchase": windows.days_since_purchase,
        "days_since_delivery": windows.days_since_delivery,
        "withdrawal_online": deadline(windows.withdrawal),
        "defect_exchange": deadline(windows.defect_exchange),
        "preference_exchange": deadline(windows.preference_exchange),
        "legal_warranty": deadline(windows.legal_warranty),
    }


def _today(context: ToolContext) -> date:
    return context.now.astimezone(rules.STORE_TZ).date()


def _iso_date(value: Any) -> str | None:
    return value.date().isoformat() if pd.notna(value) else None

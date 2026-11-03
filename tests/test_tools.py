import json
from datetime import datetime
from pathlib import Path
from typing import Any, get_args

import pytest

from emporio.data_loader import StoreData, load_store_data
from emporio.rules import STORE_TZ
from emporio.tools import (
    ORDER_NOT_FOUND,
    TOOLS,
    CategoryName,
    ToolContext,
    describe_payment,
    execute_tool,
    is_local_city,
    normalize_text,
)

# A Friday, 10:30 in Campo Grande, three days after order 7 was delivered.
NOW = datetime(2026, 2, 20, 10, 30, tzinfo=STORE_TZ)


@pytest.fixture(scope="module")
def data() -> StoreData:
    return load_store_data()


@pytest.fixture
def context(data: StoreData, tmp_path: Path) -> ToolContext:
    return ToolContext(data=data, now=NOW, tickets_dir=tmp_path / "tickets")


def call(context: ToolContext, name: str, **arguments: Any) -> dict[str, Any]:
    result = execute_tool(name, json.dumps(arguments), context)
    # Results go back to the model as JSON, so they must always serialize.
    json.dumps(result)
    return result


class TestExecuteTool:
    def test_unknown_tool_returns_error(self, context: ToolContext) -> None:
        assert "error" in execute_tool("delete_everything", "{}", context)

    def test_invalid_arguments_return_error_for_the_model(self, context: ToolContext) -> None:
        result = call(context, "get_price_quote", product_id=95, quantity=-2)
        assert result["error"] == "Invalid arguments"
        assert result["details"][0]["loc"] == ("quantity",)

    def test_unexpected_argument_is_rejected(self, context: ToolContext) -> None:
        result = call(context, "search_products", price=100)
        assert result["error"] == "Invalid arguments"

    def test_malformed_json_returns_error(self, context: ToolContext) -> None:
        assert execute_tool("search_products", "{not json", context)["error"] == "Invalid arguments"

    def test_every_tool_is_registered(self) -> None:
        assert set(TOOLS) == {
            "search_products",
            "get_order_status",
            "list_active_promotions",
            "get_price_quote",
            "create_support_ticket",
        }

    def test_category_names_match_the_data(self, data: StoreData) -> None:
        assert set(get_args(CategoryName)) == set(data.categories["name"])


class TestSearchProducts:
    def test_guitars_up_to_one_thousand(self, context: ToolContext) -> None:
        result = call(context, "search_products", category="Violões", max_price=1000)
        assert result["total_found"] == 14
        assert result["shown"] == 10
        assert all(product["list_price"] <= 1000 for product in result["products"])
        assert all(product["availability"] == "available" for product in result["products"])

    def test_ignores_accents_and_case(self, context: ToolContext) -> None:
        result = call(context, "search_products", query="VIOLAO")
        assert result["total_found"] == 35
        assert {product["category"] for product in result["products"]} == {"Violões"}

    def test_finds_takamine_gd20(self, context: ToolContext) -> None:
        products = call(context, "search_products", query="takamine gd20")["products"]
        assert [product["product_id"] for product in products] == [95]
        assert products[0]["availability"] == "available"
        assert products[0]["promotion"] is None

    def test_brand_filter(self, context: ToolContext) -> None:
        products = call(context, "search_products", brand="yamaha")["products"]
        assert products
        assert {product["brand"] for product in products} == {"Yamaha"}

    def test_max_price_uses_promotional_price(self, context: ToolContext) -> None:
        ids = [p["product_id"] for p in call(context, "search_products", query="crafter", max_price=2000)["products"]]
        assert 90 in ids  # list price R$ 2.399, promotional R$ 1.967,18

    def test_discontinued_and_out_of_stock_are_flagged_and_listed_last(self, context: ToolContext) -> None:
        products = call(context, "search_products", query="7 cordas nylon")["products"]
        assert products[-1]["availability"] == "discontinued"

    def test_no_match(self, context: ToolContext) -> None:
        assert call(context, "search_products", query="theremin")["total_found"] == 0


class TestGetOrderStatus:
    def test_matching_email_ignores_case_and_spaces(self, context: ToolContext) -> None:
        result = call(context, "get_order_status", order_id=7, email="  Leticia.Rocha@JMAIL.com ")
        assert result["found"]
        assert result["status"] == "delivered"
        assert result["delivered_on"] == "2026-02-17"
        assert result["tracking_code"] == "BRKR1122334BR"
        assert result["payment"] == "cartão de crédito em 3x"
        assert result["items"] == [{"name": "Shelby SU-21S Soprano Sunburst", "quantity": 1}]

    def test_delivered_order_has_deadlines(self, context: ToolContext) -> None:
        windows = call(context, "get_order_status", order_id=7, email="leticia.rocha@jmail.com")["return_windows"]
        assert windows["days_since_delivery"] == 3
        assert windows["withdrawal_online"] == {"last_day": "2026-02-24", "is_open": True}

    def test_withdrawal_period_over(self, data: StoreData, tmp_path: Path) -> None:
        later = ToolContext(data=data, now=datetime(2026, 3, 1, 10, tzinfo=STORE_TZ), tickets_dir=tmp_path)
        windows = call(later, "get_order_status", order_id=7, email="leticia.rocha@jmail.com")["return_windows"]
        assert not windows["withdrawal_online"]["is_open"]
        assert windows["defect_exchange"]["is_open"]

    def test_wrong_email_looks_like_missing_order(self, context: ToolContext) -> None:
        wrong_email = call(context, "get_order_status", order_id=7, email="outra@pessoa.com")
        missing = call(context, "get_order_status", order_id=999, email="leticia.rocha@jmail.com")
        assert wrong_email == missing == {"found": False, "message": ORDER_NOT_FOUND}

    def test_pending_order_has_no_tracking_or_deadlines(self, context: ToolContext) -> None:
        result = call(context, "get_order_status", order_id=13, email="fernanda.ribeiro@coldmail.com")
        assert result["status"] == "pending"
        assert result["tracking_code"] is None
        assert result["return_windows"] is None

    def test_does_not_expose_contact_data(self, context: ToolContext) -> None:
        result = call(context, "get_order_status", order_id=7, email="leticia.rocha@jmail.com")
        assert not {"email", "phone", "address", "customer_id"} & set(result)


class TestListActivePromotions:
    def test_lists_active_promotions_largest_first(self, context: ToolContext) -> None:
        promotions = call(context, "list_active_promotions")["promotions"]
        assert [product["product_id"] for product in promotions] == [121, 90, 127, 94]
        assert promotions[0]["promotion"] == {
            "name": "Desconto Exclusivo",
            "discount_percent": 20,
            "promotional_price": 439.2,
        }


class TestGetPriceQuote:
    def test_takamine_in_campo_grande(self, context: ToolContext) -> None:
        quote = call(context, "get_price_quote", product_id=95, city="Campo Grande - MS")
        assert quote["shipping"]["fee"] == 0.0
        assert quote["debit_or_boleto_total"] == 2199.0
        assert quote["pix_total"] == 2089.05
        assert quote["credit_card_installments"][-1] == {"count": 12, "value": 183.25}
        assert quote["can_combine_payment_methods"]

    def test_no_pix_discount_on_promotional_price(self, context: ToolContext) -> None:
        quote = call(context, "get_price_quote", product_id=121)
        assert quote["goods_total"] == 439.2
        assert quote["pix_total"] is None
        assert "6.2" in quote["pix_note"]

    def test_local_fee_below_five_hundred(self, context: ToolContext) -> None:
        quote = call(context, "get_price_quote", product_id=119, city="campo grande")
        assert quote["shipping"]["fee"] == 35.0
        assert quote["debit_or_boleto_total"] == 194.9
        assert quote["pix_total"] == 186.91  # 151.91 + 35.00

    def test_other_city_lists_carriers_without_price(self, context: ToolContext) -> None:
        quote = call(context, "get_price_quote", product_id=95, city="Dourados")
        assert quote["shipping"]["fee"] is None
        assert len(quote["shipping"]["options"]) == 3
        assert not quote["shipping_included_in_totals"]

    def test_large_instrument_may_need_special_quote(self, context: ToolContext) -> None:
        quote = call(context, "get_price_quote", product_id=140, city="Corumbá")
        assert quote["shipping"]["special_quote_may_be_required"]

    def test_quantity_above_stock(self, context: ToolContext) -> None:
        assert not call(context, "get_price_quote", product_id=93, quantity=3)["enough_stock"]

    def test_unknown_product(self, context: ToolContext) -> None:
        assert "error" in call(context, "get_price_quote", product_id=9999)


class TestCreateSupportTicket:
    def test_creates_sequential_protocols_in_a_local_file(self, context: ToolContext) -> None:
        first = call(context, "create_support_ticket", ticket_type="complaint", summary="Entrega atrasada há uma semana")
        second = call(context, "create_support_ticket", ticket_type="data_deletion", summary="Quer excluir seus dados")
        assert first["protocol"] == "EM-20260220-0001"
        assert second["protocol"] == "EM-20260220-0002"
        lines = (context.tickets_dir / "tickets.jsonl").read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[1])["ticket_type"] == "data_deletion"

    def test_deadline_is_twenty_four_business_hours(self, context: ToolContext) -> None:
        # Friday 10:30: 7.5h Friday, 4h Saturday, Sunday closed, 9h Monday, 3.5h Tuesday.
        result = call(context, "create_support_ticket", ticket_type="complaint", summary="Produto chegou riscado")
        assert result["response_deadline"] == "2026-02-24T12:30-04:00"
        assert result["store_open_now"]
        assert result["team_back_at"] is None

    def test_closed_store_reports_when_team_is_back(self, data: StoreData, tmp_path: Path) -> None:
        sunday = ToolContext(data=data, now=datetime(2026, 2, 22, 15, tzinfo=STORE_TZ), tickets_dir=tmp_path)
        result = call(sunday, "create_support_ticket", ticket_type="complaint", summary="Produto chegou riscado")
        assert not result["store_open_now"]
        assert result["team_back_at"] == "2026-02-23T09:00-04:00"


class TestHelpers:
    def test_normalize_text(self) -> None:
        assert normalize_text("  Violão Elétrico ") == "violao eletrico"

    @pytest.mark.parametrize(
        ("raw", "text"),
        [("pix", "PIX"), ("credit_12x", "cartão de crédito em 12x"), ("boleto", "boleto")],
    )
    def test_describe_payment(self, raw: str, text: str) -> None:
        assert describe_payment(raw) == text

    @pytest.mark.parametrize(
        ("city", "is_local"),
        [("Campo Grande", True), ("campo grande/MS", True), ("Campo Grande - MS", True), ("Dourados", False)],
    )
    def test_is_local_city(self, city: str, is_local: bool) -> None:
        assert is_local_city(city) is is_local

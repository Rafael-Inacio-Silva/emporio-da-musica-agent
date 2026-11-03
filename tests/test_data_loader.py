import shutil
from pathlib import Path

import pandas as pd
import pytest

from emporio.data_loader import (
    DATA_DIR,
    DataValidationError,
    StoreData,
    clean_tracking_code,
    extract_brand,
    is_valid_tracking_code,
    load_store_data,
    normalize_email,
)


@pytest.fixture(scope="module")
def data() -> StoreData:
    return load_store_data()


@pytest.fixture
def data_copy(tmp_path: Path) -> Path:
    for csv_file in DATA_DIR.glob("*.csv"):
        shutil.copy(csv_file, tmp_path)
    return tmp_path


def _rewrite_csv(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")


class TestRealData:
    def test_loads_every_row(self, data: StoreData) -> None:
        assert len(data.products) == 65
        assert len(data.categories) == 9
        assert len(data.customers) == 50
        assert len(data.orders) == 20
        assert len(data.order_items) == 22
        assert len(data.promotions) == 25

    def test_dates_are_parsed(self, data: StoreData) -> None:
        assert pd.api.types.is_datetime64_any_dtype(data.orders["order_date"])
        assert pd.api.types.is_datetime64_any_dtype(data.orders["estimated_delivery"])
        assert pd.api.types.is_datetime64_any_dtype(data.products["created_at"])

    def test_every_product_has_brand_and_category(self, data: StoreData) -> None:
        assert data.products["brand"].notna().all()
        assert data.products["category"].notna().all()

    def test_specs_are_dicts(self, data: StoreData) -> None:
        assert data.products["specs"].map(lambda specs: isinstance(specs, dict)).all()

    def test_customer_phone_is_not_loaded(self, data: StoreData) -> None:
        assert "phone" not in data.customers.columns

    def test_emails_are_normalized(self, data: StoreData) -> None:
        assert (data.customers["email"] == data.customers["email"].map(normalize_email)).all()

    def test_dispatched_orders_have_valid_tracking(self, data: StoreData) -> None:
        dispatched = data.orders[data.orders["status"].isin({"shipped", "delivered"})]
        assert dispatched["tracking_code"].map(is_valid_tracking_code).all()

    def test_orders_not_dispatched_have_no_tracking(self, data: StoreData) -> None:
        pending = data.orders[~data.orders["status"].isin({"shipped", "delivered"})]
        assert pending["tracking_code"].isna().all()
        assert pending["estimated_delivery"].isna().all()

    def test_is_active_is_boolean(self, data: StoreData) -> None:
        assert data.promotions["is_active"].dtype == bool
        assert data.promotions["is_active"].sum() == 4

    def test_takamine_gd20_is_in_stock_without_active_promotion(self, data: StoreData) -> None:
        takamine = data.products[data.products["name"].str.contains("Takamine GD20")].iloc[0]
        promotions = data.promotions[data.promotions["product_id"] == takamine["product_id"]]
        assert takamine["status"] == "active"
        assert takamine["stock_quantity"] > 0
        assert not promotions["is_active"].any()


class TestTrackingCode:
    @pytest.mark.parametrize("code", ["BRAB1234567BR", "BR4K7M2X9P1BR"])
    def test_valid_codes(self, code: str) -> None:
        assert is_valid_tracking_code(code)

    @pytest.mark.parametrize(
        "code",
        ["BRAB123456BR", "BRAB12345678BR", "XXAB1234567BR", "BRAB1234567XX", "BRAB-234567BR"],
    )
    def test_invalid_codes(self, code: str) -> None:
        assert not is_valid_tracking_code(code)

    def test_clean_normalizes_case_and_spaces(self) -> None:
        assert clean_tracking_code(" brab1234567br ") == "BRAB1234567BR"

    def test_clean_discards_malformed_code(self) -> None:
        assert clean_tracking_code("BR123BR") is None

    def test_clean_keeps_missing_as_none(self) -> None:
        assert clean_tracking_code(float("nan")) is None

    def test_malformed_code_in_csv_becomes_missing(self, data_copy: Path) -> None:
        _rewrite_csv(data_copy / "orders.csv", "BRAB1234567BR", "BRAB12BR")
        orders = load_store_data(data_copy).orders
        assert pd.isna(orders.loc[orders["order_id"] == 1, "tracking_code"].iloc[0])


class TestBrand:
    @pytest.mark.parametrize(
        ("name", "brand"),
        [
            ("Kala KA-15S Soprano Mogno", "Kala"),
            ("Kalani KAL-300S Soprano Natural", "Kalani"),
            ("Music Man Bass 1X Electric Bass", "Music Man"),
            ("Bateria Acústica Pearl Kit 2 Studio", "Pearl"),
        ],
    )
    def test_extracts_brand_from_name(self, name: str, brand: str) -> None:
        assert extract_brand(name) == brand

    def test_unknown_brand_returns_none(self) -> None:
        assert extract_brand("Violão Genérico") is None


class TestStructuralErrors:
    def test_missing_file_raises(self, data_copy: Path) -> None:
        (data_copy / "orders.csv").unlink()
        with pytest.raises(FileNotFoundError):
            load_store_data(data_copy)

    def test_duplicated_product_id_raises(self, data_copy: Path) -> None:
        _rewrite_csv(data_copy / "products.csv", "\n82,", "\n81,")
        with pytest.raises(DataValidationError, match="duplicated product_id"):
            load_store_data(data_copy)

    def test_unknown_order_status_raises(self, data_copy: Path) -> None:
        _rewrite_csv(data_copy / "orders.csv", ",delivered,", ",lost,")
        with pytest.raises(DataValidationError, match="unknown status"):
            load_store_data(data_copy)

    def test_order_item_with_unknown_product_raises(self, data_copy: Path) -> None:
        _rewrite_csv(data_copy / "order_items.csv", "1,1,93", "1,1,999")
        with pytest.raises(DataValidationError, match="product_id not found"):
            load_store_data(data_copy)

    def test_two_active_promotions_for_one_product_raise(self, data_copy: Path) -> None:
        # Product 127 already has an active promotion (id 12).
        _rewrite_csv(data_copy / "promotions.csv", "1,102,10,Black Friday,0", "1,127,10,Black Friday,1")
        with pytest.raises(DataValidationError, match="more than one active promotion"):
            load_store_data(data_copy)

    def test_bad_date_raises(self, data_copy: Path) -> None:
        _rewrite_csv(data_copy / "orders.csv", "2025-10-15", "15/10/2025")
        with pytest.raises(ValueError):
            load_store_data(data_copy)

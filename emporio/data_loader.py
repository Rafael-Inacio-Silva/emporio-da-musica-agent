"""Load the store CSV files into typed, validated pandas DataFrames.

Structural problems (missing columns, duplicate ids, unknown statuses, broken
references) raise DataValidationError at startup, so the agent never answers
a customer from data it cannot trust. Content problems that have a safe fix
(for example a malformed tracking code) are cleaned and logged instead.
"""

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Policy 5.3: "BR" + 9 alphanumeric characters + "BR"
TRACKING_CODE_PATTERN = re.compile(r"BR[A-Z0-9]{9}BR")

# The CSVs have no brand column, so the brand is read from the product name.
KNOWN_BRANDS = (
    "Crafter", "Fender", "Giannini", "Gibson", "Ibanez", "Kala", "Kalani",
    "Korg", "Martin", "Music Man", "Nord", "Ohana", "Pearl", "PRS", "Roland",
    "Rozini", "Shelby", "Tagima", "Takamine", "Taylor", "Yamaha",
)
_BRAND_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(brand) for brand in KNOWN_BRANDS) + r")\b"
)

PRODUCT_STATUSES = frozenset({"active", "discontinued", "coming_soon"})
ORDER_STATUSES = frozenset({"pending", "confirmed", "shipped", "delivered", "cancelled"})
DISPATCHED_STATUSES = frozenset({"shipped", "delivered"})

DATE_FORMAT = "%Y-%m-%d"


class DataValidationError(ValueError):
    """Raised when a CSV file breaks an assumption the agent relies on."""


@dataclass(frozen=True)
class StoreData:
    """Clean store tables, loaded once at startup."""

    products: pd.DataFrame
    categories: pd.DataFrame
    customers: pd.DataFrame
    orders: pd.DataFrame
    order_items: pd.DataFrame
    promotions: pd.DataFrame


def load_store_data(data_dir: Path = DATA_DIR) -> StoreData:
    """Load and clean every store table.

    Args:
        data_dir: Folder that contains the store CSV files.

    Returns:
        The cleaned tables.

    Raises:
        FileNotFoundError: If a CSV file is missing.
        DataValidationError: If a table breaks a structural rule.
    """
    categories = load_categories(data_dir)
    products = load_products(data_dir, categories)
    customers = load_customers(data_dir)
    orders = load_orders(data_dir, customers)
    order_items = load_order_items(data_dir, orders, products)
    promotions = load_promotions(data_dir, products)
    return StoreData(
        products=products,
        categories=categories,
        customers=customers,
        orders=orders,
        order_items=order_items,
        promotions=promotions,
    )


def load_categories(data_dir: Path) -> pd.DataFrame:
    """Load the product categories.

    Args:
        data_dir: Folder that contains categories.csv.

    Returns:
        One row per category.

    Raises:
        DataValidationError: If ids are duplicated or columns are missing.
    """
    categories = _read_csv(data_dir / "categories.csv", ["category_id", "name", "description"])
    _require_unique(categories, "category_id", "categories")
    return categories


def load_products(data_dir: Path, categories: pd.DataFrame) -> pd.DataFrame:
    """Load the product catalog, adding category name and brand.

    Args:
        data_dir: Folder that contains products.csv.
        categories: Cleaned categories table.

    Returns:
        One row per product, with ``specs`` parsed into a dict.

    Raises:
        DataValidationError: If a structural rule is broken.
    """
    products = _read_csv(
        data_dir / "products.csv",
        ["product_id", "price_brl", "name", "category_id", "description",
         "stock_quantity", "status", "specs", "created_at"],
    )
    _require_unique(products, "product_id", "products")
    _require_allowed(products, "status", PRODUCT_STATUSES, "products")
    _require_references(products, "category_id", categories["category_id"], "products")
    if (products["price_brl"] <= 0).any():
        raise DataValidationError("products: price_brl must be positive")
    if (products["stock_quantity"] < 0).any():
        raise DataValidationError("products: stock_quantity cannot be negative")

    products["specs"] = products["specs"].map(json.loads)
    products["created_at"] = _parse_dates(products["created_at"])
    category_names = categories.set_index("category_id")["name"]
    products["category"] = products["category_id"].map(category_names)
    products["brand"] = products["name"].map(extract_brand)
    for name in products.loc[products["brand"].isna(), "name"]:
        logger.warning("products: no known brand in name %r", name)
    return products.sort_values("product_id", ignore_index=True)


def load_customers(data_dir: Path) -> pd.DataFrame:
    """Load customers without their phone numbers.

    Args:
        data_dir: Folder that contains customers.csv.

    Returns:
        One row per customer, with normalized e-mail.

    Raises:
        DataValidationError: If ids are duplicated or columns are missing.
    """
    # The phone is never needed to answer a customer, so it is not even
    # loaded (LGPD data minimization, policy 9).
    customers = _read_csv(data_dir / "customers.csv", ["customer_id", "name", "email", "city"])
    _require_unique(customers, "customer_id", "customers")
    customers["email"] = customers["email"].map(normalize_email)
    return customers


def load_orders(data_dir: Path, customers: pd.DataFrame) -> pd.DataFrame:
    """Load orders with parsed dates and validated tracking codes.

    Args:
        data_dir: Folder that contains orders.csv.
        customers: Cleaned customers table.

    Returns:
        One row per order. Missing tracking codes, delivery dates and notes
        are NA, which is expected for orders not yet dispatched.

    Raises:
        DataValidationError: If a structural rule is broken.
    """
    orders = _read_csv(
        data_dir / "orders.csv",
        ["order_id", "customer_id", "order_date", "status", "total_brl",
         "payment_method", "tracking_code", "estimated_delivery", "notes"],
    )
    _require_unique(orders, "order_id", "orders")
    _require_allowed(orders, "status", ORDER_STATUSES, "orders")
    _require_references(orders, "customer_id", customers["customer_id"], "orders")

    orders["order_date"] = _parse_dates(orders["order_date"])
    orders["estimated_delivery"] = _parse_dates(orders["estimated_delivery"])
    orders["tracking_code"] = orders["tracking_code"].map(clean_tracking_code)
    _warn_dispatched_without_tracking(orders)
    return orders


def load_order_items(
    data_dir: Path, orders: pd.DataFrame, products: pd.DataFrame
) -> pd.DataFrame:
    """Load order lines.

    Args:
        data_dir: Folder that contains order_items.csv.
        orders: Cleaned orders table.
        products: Cleaned products table.

    Returns:
        One row per product in an order.

    Raises:
        DataValidationError: If a structural rule is broken.
    """
    items = _read_csv(data_dir / "order_items.csv", ["order_id", "quantity", "product_id"])
    _require_references(items, "order_id", orders["order_id"], "order_items")
    _require_references(items, "product_id", products["product_id"], "order_items")
    if (items["quantity"] <= 0).any():
        raise DataValidationError("order_items: quantity must be positive")
    if items.duplicated(["order_id", "product_id"]).any():
        raise DataValidationError("order_items: product repeated in the same order")
    return items


def load_promotions(data_dir: Path, products: pd.DataFrame) -> pd.DataFrame:
    """Load promotions with ``is_active`` as a boolean.

    Args:
        data_dir: Folder that contains promotions.csv.
        products: Cleaned products table.

    Returns:
        One row per promotion, active or not.

    Raises:
        DataValidationError: If a structural rule is broken.
    """
    promotions = _read_csv(
        data_dir / "promotions.csv",
        ["promotion_id", "product_id", "discount_percent", "description", "is_active"],
    )
    _require_unique(promotions, "promotion_id", "promotions")
    _require_references(promotions, "product_id", products["product_id"], "promotions")
    _require_allowed(promotions, "is_active", {0, 1}, "promotions")
    if not promotions["discount_percent"].between(1, 99).all():
        raise DataValidationError("promotions: discount_percent must be between 1 and 99")

    promotions["is_active"] = promotions["is_active"].astype(bool)
    # Promotions do not stack (policy 6.2), so two active ones for the same
    # product would leave the price undefined.
    active_per_product = promotions.loc[promotions["is_active"], "product_id"].value_counts()
    if (active_per_product > 1).any():
        raise DataValidationError("promotions: more than one active promotion for a product")
    return promotions


def extract_brand(product_name: str) -> str | None:
    """Return the first known brand that appears in a product name.

    Args:
        product_name: Product name as written in the catalog.

    Returns:
        The brand, or None if the name has no known brand.
    """
    match = _BRAND_PATTERN.search(product_name)
    return match.group(1) if match else None


def normalize_email(email: str) -> str:
    """Normalize an e-mail for comparison.

    Args:
        email: E-mail as typed or stored.

    Returns:
        The e-mail in lowercase, without surrounding spaces.
    """
    return email.strip().lower()


def is_valid_tracking_code(code: str) -> bool:
    """Check a tracking code against the store format (policy 5.3).

    Args:
        code: Tracking code, already uppercase and without spaces.

    Returns:
        True if the code is "BR" + 9 alphanumeric characters + "BR".
    """
    return TRACKING_CODE_PATTERN.fullmatch(code) is not None


def clean_tracking_code(code: object) -> str | None:
    """Normalize a tracking code, discarding it if it breaks the format.

    A malformed code is dropped rather than shown, because sending the
    customer to track an invalid code is worse than saying it is unavailable.

    Args:
        code: Raw value from the CSV (string or NA).

    Returns:
        The uppercase code, or None if it is missing or malformed.
    """
    if pd.isna(code):
        return None
    normalized = str(code).strip().upper()
    if not is_valid_tracking_code(normalized):
        logger.warning("orders: discarding malformed tracking code %r", code)
        return None
    return normalized


def _read_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing = set(columns) - set(frame.columns)
    if missing:
        raise DataValidationError(f"{path.name}: missing columns {sorted(missing)}")
    frame = frame[columns]
    text_columns = frame.select_dtypes(include="str").columns
    frame[text_columns] = frame[text_columns].apply(lambda column: column.str.strip())
    return frame


def _parse_dates(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, format=DATE_FORMAT)


def _require_unique(frame: pd.DataFrame, column: str, table: str) -> None:
    duplicated = frame.loc[frame[column].duplicated(), column].tolist()
    if duplicated:
        raise DataValidationError(f"{table}: duplicated {column} {duplicated}")


def _require_allowed(
    frame: pd.DataFrame, column: str, allowed: Iterable[object], table: str
) -> None:
    unknown = set(frame[column]) - set(allowed)
    if unknown:
        raise DataValidationError(f"{table}: unknown {column} values {sorted(map(str, unknown))}")


def _require_references(
    frame: pd.DataFrame, column: str, valid_ids: pd.Series, table: str
) -> None:
    broken = sorted(set(frame[column]) - set(valid_ids))
    if broken:
        raise DataValidationError(f"{table}: {column} not found {broken}")


def _warn_dispatched_without_tracking(orders: pd.DataFrame) -> None:
    dispatched = orders["status"].isin(DISPATCHED_STATUSES)
    for order_id in orders.loc[dispatched & orders["tracking_code"].isna(), "order_id"]:
        logger.warning("orders: order %s was dispatched but has no valid tracking code", order_id)

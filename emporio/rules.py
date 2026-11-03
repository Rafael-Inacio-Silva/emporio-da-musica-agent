"""Store policy rules as pure functions, citing the policy manual items.

Every number the agent shows (PIX price, installments, shipping, deadlines)
comes from here, never from the language model.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import holidays

STORE_TZ = ZoneInfo("America/Campo_Grande")

PIX_DISCOUNT = Decimal("0.05")  # policy 3
MAX_INSTALLMENTS = 12  # policy 3
COMBINED_PAYMENT_MIN_TOTAL = 2000.0  # policy 3.1

LOCAL_FREE_SHIPPING_MIN = 500.0  # policy 5.1
LOCAL_SHIPPING_FEE = 35.0  # policy 5.1
LOCAL_DELIVERY_DAYS = "1 a 3 dias úteis"  # policy 5.1
CARRIERS = (  # policy 5.2
    {"name": "PAC (Correios)", "delivery_time": "5 a 12 dias úteis"},
    {"name": "SEDEX (Correios)", "delivery_time": "2 a 5 dias úteis"},
    {"name": "Jadlog (.package)", "delivery_time": "3 a 8 dias úteis"},
)

WITHDRAWAL_DAYS = 7  # policy 4.1, counted from delivery
DEFECT_EXCHANGE_DAYS = 30  # policy 4.2, counted from purchase
PREFERENCE_EXCHANGE_DAYS = 7  # policy 4.3, counted from delivery (assumption)
LEGAL_WARRANTY_DAYS = 90  # policy 8.1, counted from delivery

TICKET_RESPONSE_BUSINESS_HOURS = 24  # policy 7.3

# Policy 2. Monday is 0; Sunday is missing because the store is closed.
OPENING_HOURS: dict[int, tuple[time, time]] = {
    **{weekday: (time(9), time(18)) for weekday in range(5)},
    5: (time(9), time(13)),
}
_HOLIDAYS = holidays.country_holidays("BR", subdiv="MS")


@dataclass(frozen=True)
class InstallmentOption:
    """One valid way to split a credit card payment."""

    count: int
    value: float


@dataclass(frozen=True)
class Deadline:
    """Last day of a policy period and whether it is still running."""

    last_day: date
    is_open: bool


@dataclass(frozen=True)
class ReturnWindows:
    """Return, exchange and warranty periods of a delivered order."""

    days_since_purchase: int
    days_since_delivery: int
    withdrawal: Deadline
    defect_exchange: Deadline
    preference_exchange: Deadline
    legal_warranty: Deadline


def return_windows(purchase_date: date, delivery_date: date, today: date) -> ReturnWindows:
    """Compute every return and exchange period of a delivered order (policy 4 and 8.1).

    Args:
        purchase_date: Day the order was placed.
        delivery_date: Day the customer received the order.
        today: Reference date.

    Returns:
        Days elapsed and the deadline of each period, all in calendar days.
    """

    def deadline(start: date, days: int) -> Deadline:
        last_day = start + timedelta(days=days)
        return Deadline(last_day=last_day, is_open=today <= last_day)

    return ReturnWindows(
        days_since_purchase=(today - purchase_date).days,
        days_since_delivery=(today - delivery_date).days,
        withdrawal=deadline(delivery_date, WITHDRAWAL_DAYS),
        defect_exchange=deadline(purchase_date, DEFECT_EXCHANGE_DAYS),
        preference_exchange=deadline(delivery_date, PREFERENCE_EXCHANGE_DAYS),
        legal_warranty=deadline(delivery_date, LEGAL_WARRANTY_DAYS),
    )


def round_money(value: float | Decimal) -> float:
    """Round a value to cents, half up, as a cashier would.

    Args:
        value: Amount in BRL.

    Returns:
        The amount rounded to two decimals.
    """
    # Binary floats cannot hold most cents exactly, so round() may round a
    # half cent down (round(2.675, 2) == 2.67). Decimal keeps it half up.
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def pix_price(list_price: float) -> float:
    """Apply the PIX discount to a list price (policy 3).

    Args:
        list_price: Price without any promotion.

    Returns:
        The PIX price, rounded to cents.
    """
    return round_money(Decimal(str(list_price)) * (1 - PIX_DISCOUNT))


def promotional_price(list_price: float, discount_percent: int) -> float:
    """Apply a promotion discount to a list price (policy 6).

    Args:
        list_price: Price without any promotion.
        discount_percent: Discount, from 1 to 99.

    Returns:
        The promotional price, rounded to cents.
    """
    return round_money(Decimal(str(list_price)) * (100 - discount_percent) / 100)


def minimum_installment(count: int) -> float:
    """Return the minimum installment value for a number of installments.

    Args:
        count: Number of installments, from 1 to 12.

    Returns:
        The minimum value each installment must reach.

    Raises:
        ValueError: If count is outside 1 to 12.
    """
    # Policy 3.1 tiers. The table in section 3 says R$ 100 for every plan,
    # but 3.1 is more specific, so its tiers win (see README assumptions).
    if not 1 <= count <= MAX_INSTALLMENTS:
        raise ValueError(f"installments must be between 1 and {MAX_INSTALLMENTS}")
    if count == 1:
        return 0.0
    if count <= 3:
        return 50.0
    if count <= 6:
        return 80.0
    return 100.0


def installment_options(total: float) -> list[InstallmentOption]:
    """List every interest-free installment plan allowed for a total.

    Args:
        total: Amount to be paid by credit card.

    Returns:
        Valid plans, from 1x up to the largest allowed.
    """
    options = []
    for count in range(1, MAX_INSTALLMENTS + 1):
        value = Decimal(str(total)) / count
        if value >= Decimal(str(minimum_installment(count))):
            options.append(InstallmentOption(count=count, value=round_money(value)))
    return options


def can_combine_payments(total: float) -> bool:
    """Check whether the customer may mix payment methods (policy 3.1).

    Args:
        total: Purchase total.

    Returns:
        True for purchases above R$ 2,000.00.
    """
    return total > COMBINED_PAYMENT_MIN_TOTAL


def local_shipping_fee(goods_total: float) -> float:
    """Return the motoboy fee inside Campo Grande (policy 5.1).

    Args:
        goods_total: Value of the products in the order.

    Returns:
        0 for orders of R$ 500.00 or more, otherwise the fixed fee.
    """
    # The policy says "above R$ 500" is free and "below R$ 500" pays the fee;
    # exactly R$ 500 is not covered, so it gets the customer-friendly reading.
    return 0.0 if goods_total >= LOCAL_FREE_SHIPPING_MIN else LOCAL_SHIPPING_FEE


def is_store_open(moment: datetime) -> bool:
    """Check whether the store is open at a given moment (policy 2).

    Args:
        moment: Timezone-aware datetime.

    Returns:
        True during opening hours on a non-holiday.
    """
    hours = opening_hours_on(moment.astimezone(STORE_TZ).date())
    return hours is not None and hours[0] <= moment < hours[1]


def next_opening(moment: datetime) -> datetime:
    """Return when the store next opens, or ``moment`` if it is open.

    Args:
        moment: Timezone-aware datetime.

    Returns:
        The opening time, in the store timezone.
    """
    if is_store_open(moment):
        return moment
    day = moment.astimezone(STORE_TZ).date()
    while True:
        hours = opening_hours_on(day)
        if hours is not None and moment < hours[0]:
            return hours[0]
        day += timedelta(days=1)


def add_business_hours(start: datetime, hours: int) -> datetime:
    """Add a number of store business hours to a moment.

    Args:
        start: Timezone-aware datetime.
        hours: Business hours to add.

    Returns:
        The moment when those hours have passed, counting only opening hours.
    """
    remaining = timedelta(hours=hours)
    day = start.astimezone(STORE_TZ).date()
    while True:
        opening = opening_hours_on(day)
        if opening is not None:
            begin = max(start, opening[0])
            available = opening[1] - begin
            if available > timedelta(0):
                if remaining <= available:
                    return begin + remaining
                remaining -= available
        day += timedelta(days=1)


def opening_hours_on(day: date) -> tuple[datetime, datetime] | None:
    """Return the opening and closing times of a day.

    Args:
        day: Calendar day in the store timezone.

    Returns:
        (opening, closing) as aware datetimes, or None on Sundays and
        national or Mato Grosso do Sul holidays.
    """
    if day in _HOLIDAYS or day.weekday() not in OPENING_HOURS:
        return None
    opens, closes = OPENING_HOURS[day.weekday()]
    return (
        datetime.combine(day, opens, tzinfo=STORE_TZ),
        datetime.combine(day, closes, tzinfo=STORE_TZ),
    )

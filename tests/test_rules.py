from datetime import date, datetime

import pytest

from emporio import rules
from emporio.rules import STORE_TZ


def at(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=STORE_TZ)


class TestMoney:
    def test_rounds_half_cent_up(self) -> None:
        assert rules.round_money(2.675) == 2.68

    def test_pix_discount_is_five_percent(self) -> None:
        assert rules.pix_price(1000) == 950.0
        assert rules.pix_price(2199) == 2089.05

    def test_pix_rounds_half_cent_up(self) -> None:
        assert rules.pix_price(159.9) == 151.91

    def test_promotional_price(self) -> None:
        assert rules.promotional_price(2399, 18) == 1967.18
        assert rules.promotional_price(549, 20) == 439.2


class TestInstallments:
    @pytest.mark.parametrize(
        ("count", "minimum"), [(1, 0), (2, 50), (3, 50), (4, 80), (6, 80), (7, 100), (12, 100)]
    )
    def test_minimum_per_tier(self, count: int, minimum: float) -> None:
        assert rules.minimum_installment(count) == minimum

    @pytest.mark.parametrize("count", [0, 13])
    def test_count_out_of_range_raises(self, count: int) -> None:
        with pytest.raises(ValueError):
            rules.minimum_installment(count)

    def test_one_thousand_goes_up_to_ten_times(self) -> None:
        options = rules.installment_options(1000)
        assert options[-1] == rules.InstallmentOption(count=10, value=100.0)

    def test_twelve_times_needs_one_hundred_per_installment(self) -> None:
        assert rules.installment_options(1200)[-1].count == 12
        assert rules.installment_options(1199.99)[-1].count == 11

    def test_cheap_item_stops_at_three_times(self) -> None:
        counts = [option.count for option in rules.installment_options(159.9)]
        assert counts == [1, 2, 3]

    def test_below_fifty_only_one_time(self) -> None:
        assert [option.count for option in rules.installment_options(90)] == [1]


class TestCombinedPayment:
    def test_only_above_two_thousand(self) -> None:
        assert not rules.can_combine_payments(2000)
        assert rules.can_combine_payments(2000.01)


class TestLocalShipping:
    @pytest.mark.parametrize(("goods_total", "fee"), [(499.99, 35.0), (500, 0.0), (2199, 0.0)])
    def test_free_from_five_hundred(self, goods_total: float, fee: float) -> None:
        assert rules.local_shipping_fee(goods_total) == fee


class TestReturnWindows:
    PURCHASE = date(2026, 2, 3)
    DELIVERY = date(2026, 2, 17)

    def test_withdrawal_open_on_seventh_day_after_delivery(self) -> None:
        windows = rules.return_windows(self.PURCHASE, self.DELIVERY, date(2026, 2, 24))
        assert windows.days_since_delivery == 7
        assert windows.withdrawal == rules.Deadline(last_day=date(2026, 2, 24), is_open=True)

    def test_withdrawal_closed_on_eighth_day(self) -> None:
        windows = rules.return_windows(self.PURCHASE, self.DELIVERY, date(2026, 2, 25))
        assert not windows.withdrawal.is_open
        assert not windows.preference_exchange.is_open

    def test_defect_exchange_counts_from_purchase(self) -> None:
        windows = rules.return_windows(self.PURCHASE, self.DELIVERY, date(2026, 3, 5))
        assert windows.days_since_purchase == 30
        assert windows.defect_exchange.is_open
        assert not rules.return_windows(self.PURCHASE, self.DELIVERY, date(2026, 3, 6)).defect_exchange.is_open

    def test_legal_warranty_is_ninety_days_from_delivery(self) -> None:
        windows = rules.return_windows(self.PURCHASE, self.DELIVERY, date(2026, 3, 6))
        assert windows.legal_warranty.last_day == date(2026, 5, 18)
        assert windows.legal_warranty.is_open


class TestOpeningHours:
    @pytest.mark.parametrize(
        ("moment", "is_open"),
        [
            (at(2026, 3, 2, 9, 0), True),  # Monday opening
            (at(2026, 3, 2, 17, 59), True),
            (at(2026, 3, 2, 18, 0), False),  # Monday closing
            (at(2026, 3, 7, 12, 59), True),  # Saturday
            (at(2026, 3, 7, 13, 0), False),
            (at(2026, 3, 8, 10, 0), False),  # Sunday
            (at(2026, 4, 21, 10, 0), False),  # Tiradentes (national holiday)
            (at(2027, 10, 11, 10, 0), False),  # Criação do Estado (MS holiday)
        ],
    )
    def test_is_store_open(self, moment: datetime, is_open: bool) -> None:
        assert rules.is_store_open(moment) is is_open

    def test_next_opening_when_open_is_now(self) -> None:
        assert rules.next_opening(at(2026, 3, 2, 10, 0)) == at(2026, 3, 2, 10, 0)

    def test_next_opening_skips_sunday(self) -> None:
        assert rules.next_opening(at(2026, 3, 7, 14, 0)) == at(2026, 3, 9, 9, 0)

    def test_next_opening_skips_holiday(self) -> None:
        assert rules.next_opening(at(2026, 4, 20, 19, 0)) == at(2026, 4, 22, 9, 0)

    def test_next_opening_same_day_before_opening(self) -> None:
        assert rules.next_opening(at(2026, 3, 2, 7, 0)) == at(2026, 3, 2, 9, 0)


class TestBusinessHours:
    def test_counts_only_opening_hours(self) -> None:
        # Monday 10:00: 8h on Monday + 9h on Tuesday + 7h on Wednesday.
        assert rules.add_business_hours(at(2026, 3, 2, 10, 0), 24) == at(2026, 3, 4, 16, 0)

    def test_saturday_and_sunday(self) -> None:
        # Saturday 12:30: 0.5h on Saturday, Sunday closed, then 9h + 9h + 5.5h.
        assert rules.add_business_hours(at(2026, 2, 21, 12, 30), 24) == at(2026, 2, 25, 14, 30)

    def test_starting_while_closed(self) -> None:
        assert rules.add_business_hours(at(2026, 3, 8, 20, 0), 24) == at(2026, 3, 11, 15, 0)

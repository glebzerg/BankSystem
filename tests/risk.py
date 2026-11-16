"""Проверки базовых правил риска: python -B -m unittest tests.risk -v."""

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

from bank.accounts import Currency
from bank.exceptions import InvalidOperationError
from bank.risk import RiskAnalyzer, RiskLevel


class RiskAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = RiskAnalyzer()
        self.day = datetime(2026, 11, 17, 12, tzinfo=RiskAnalyzer.BANK_TIMEZONE)

    def test_normal_large_night_and_combined_operations(self):
        for amount, hour, level, score, reasons in (
            ("100", 12, RiskLevel.LOW, 0, ()),
            ("100000", 12, RiskLevel.MEDIUM, 2, ("large_amount",)),
            ("100", 1, RiskLevel.MEDIUM, 1, ("night_operation",)),
            ("100000", 1, RiskLevel.HIGH, 3, ("large_amount", "night_operation")),
        ):
            with self.subTest(amount=amount, hour=hour):
                result = self.analyzer.analyze(Decimal(amount), Currency.RUB,
                                               occurred_at=self.day.replace(hour=hour))
                self.assertIs(result.level, level)
                self.assertEqual(result.score, score)
                self.assertEqual(result.reasons, reasons)

    def test_large_amount_boundary_for_every_currency(self):
        for currency, threshold in (
            (Currency.RUB, "100000"), (Currency.USD, "1000"),
            (Currency.EUR, "1000"), (Currency.KZT, "500000"), (Currency.CNY, "10000"),
        ):
            for difference in (Decimal("-0.01"), Decimal("0"), Decimal("0.01")):
                with self.subTest(currency=currency, difference=difference):
                    result = self.analyzer.analyze(Decimal(threshold) + difference, currency,
                                                   occurred_at=self.day)
                    self.assertEqual("large_amount" in result.reasons, difference >= 0)

    def test_night_boundaries(self):
        for hour, minute, second, expected in (
            (23, 59, 59, False), (0, 0, 0, True),
            (4, 59, 59, True), (5, 0, 0, False),
        ):
            with self.subTest(hour=hour):
                time = self.day.replace(hour=hour, minute=minute, second=second)
                result = self.analyzer.analyze(Decimal("1"), Currency.RUB, occurred_at=time)
                self.assertEqual("night_operation" in result.reasons, expected)

    def test_timezone_conversion_crosses_date_boundary(self):
        # 21:00 UTC — это 00:00 следующего дня по Москве.
        time = datetime(2026, 11, 17, 21, tzinfo=timezone.utc)
        result = self.analyzer.analyze(Decimal("1"), Currency.RUB, occurred_at=time)
        self.assertEqual(result.reasons, ("night_operation",))
        local = time.astimezone(RiskAnalyzer.BANK_TIMEZONE)
        self.assertEqual(result, self.analyzer.analyze(Decimal("1"), Currency.RUB,
                                                      occurred_at=local))

    def test_custom_night_interval_wraps_midnight(self):
        analyzer = RiskAnalyzer(night_start_hour=22, night_end_hour=6)
        for hour, expected in ((21, False), (22, True), (0, True), (5, True), (6, False)):
            with self.subTest(hour=hour):
                result = analyzer.analyze(Decimal("1"), Currency.RUB,
                                          occurred_at=self.day.replace(hour=hour))
                self.assertEqual("night_operation" in result.reasons, expected)

    def test_custom_amount_and_level_thresholds(self):
        thresholds = {Currency.RUB: Decimal("500")}
        analyzer = RiskAnalyzer(large_amount_thresholds=thresholds, medium_score=2, high_score=3)
        thresholds[Currency.RUB] = Decimal("999999")
        large = analyzer.analyze(Decimal("500"), Currency.RUB, occurred_at=self.day)
        self.assertIs(large.level, RiskLevel.MEDIUM)
        night = analyzer.analyze(Decimal("1"), Currency.RUB,
                                 occurred_at=self.day.replace(hour=1))
        self.assertIs(night.level, RiskLevel.LOW)
        self.assertEqual(night.reasons, ("night_operation",))
        combined = analyzer.analyze(Decimal("500"), Currency.RUB,
                                    occurred_at=self.day.replace(hour=1))
        self.assertIs(combined.level, RiskLevel.HIGH)
        # Частичная настройка RUB не убирает пороги остальных валют.
        usd = analyzer.analyze(Decimal("1000"), Currency.USD, occurred_at=self.day)
        self.assertEqual(usd.reasons, ("large_amount",))

    def test_result_is_immutable_and_analysis_has_no_history(self):
        first = self.analyzer.analyze(Decimal("1"), Currency.RUB, occurred_at=self.day)
        with self.assertRaises(FrozenInstanceError):
            first.score = 100
        self.assertIsInstance(first.reasons, tuple)
        self.analyzer.analyze(Decimal("100000"), Currency.RUB, occurred_at=self.day)
        self.assertEqual(first, self.analyzer.analyze(Decimal("1"), Currency.RUB,
                                                     occurred_at=self.day))

    def test_invalid_operation_data(self):
        for amount in (None, 100, "100", Decimal("0"), Decimal("-1"),
                       Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity")):
            with self.subTest(amount=amount), self.assertRaises(InvalidOperationError):
                self.analyzer.analyze(amount, Currency.RUB, occurred_at=self.day)
        for currency in (None, "RUB", 1):
            with self.subTest(currency=currency), self.assertRaises(InvalidOperationError):
                self.analyzer.analyze(Decimal("1"), currency, occurred_at=self.day)
        for time in (None, "today", datetime(2026, 11, 17)):
            with self.subTest(time=time), self.assertRaises(InvalidOperationError):
                self.analyzer.analyze(Decimal("1"), Currency.RUB, occurred_at=time)

    def test_invalid_configuration(self):
        for kwargs in (
            {"large_amount_thresholds": []},
            {"large_amount_thresholds": {"RUB": Decimal("1")}},
            *({"large_amount_thresholds": {Currency.RUB: value}} for value in (
                "100", 100, Decimal("0"), Decimal("-1"), Decimal("NaN"),
                Decimal("sNaN"), Decimal("Infinity"),
            )),
            {"night_start_hour": -1}, {"night_end_hour": 24},
            {"night_start_hour": True}, {"night_end_hour": 5.5},
            {"night_start_hour": 5, "night_end_hour": 5},
            {"medium_score": 0}, {"high_score": 1},
            {"medium_score": 4, "high_score": 3},
            {"medium_score": True}, {"high_score": 3.0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(InvalidOperationError):
                RiskAnalyzer(**kwargs)


if __name__ == "__main__":
    unittest.main()

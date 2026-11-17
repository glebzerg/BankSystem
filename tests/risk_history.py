"""Проверки частоты операций и истории получателей."""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bank.accounts import Currency
from bank.exceptions import InvalidOperationError
from bank.risk import RiskAnalyzer, RiskLevel
from bank.transactions import ExternalRecipient


class RiskHistoryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 12, tzinfo=RiskAnalyzer.BANK_TIMEZONE)
        self.analyzer = RiskAnalyzer()

    def analyze(self, operation_id="current", client_id="c1", recipient=None, **kwargs):
        return self.analyzer.analyze(
            Decimal("100"), Currency.RUB, occurred_at=self.now,
            client_id=client_id, operation_id=operation_id, recipient=recipient, **kwargs,
        )

    def attempt(self, operation_id, *, client_id="c1", seconds_ago=0):
        self.analyzer.record_attempt(client_id, operation_id,
                                     occurred_at=self.now - timedelta(seconds=seconds_ago))

    def test_frequency_includes_current_and_triggers_on_fifth_operation(self):
        for index in range(3):
            self.attempt(str(index))
        self.assertNotIn("frequent_operations", self.analyze().reasons)
        self.attempt("fourth")
        result = self.analyze()
        self.assertEqual(result.reasons, ("frequent_operations",))
        self.assertEqual(result.score, 2)
        self.assertIs(result.level, RiskLevel.MEDIUM)

    def test_retry_is_not_a_new_operation(self):
        for index in range(3):
            self.attempt(str(index))
        for _ in range(10):
            self.attempt("current")
            self.assertNotIn("frequent_operations", self.analyze().reasons)
        self.attempt("another")
        self.assertIn("frequent_operations", self.analyze().reasons)

    def test_retry_does_not_refresh_first_attempt_time(self):
        self.attempt("old", seconds_ago=61)
        self.attempt("old")
        for index in range(3):
            self.attempt(str(index))
        self.assertNotIn("frequent_operations", self.analyze().reasons)

    def test_window_excludes_lower_boundary_and_future_operations(self):
        for index in range(3):
            self.attempt(str(index))
        self.attempt("boundary", seconds_ago=60)
        self.attempt("future", seconds_ago=-1)
        self.assertNotIn("frequent_operations", self.analyze().reasons)
        self.attempt("inside", seconds_ago=59.999)
        self.assertIn("frequent_operations", self.analyze().reasons)
        self.now += timedelta(minutes=1)
        self.assertNotIn("frequent_operations", self.analyze().reasons)

    def test_frequency_compares_timezones(self):
        for index in range(4):
            self.analyzer.record_attempt("c1", str(index), occurred_at=self.now.astimezone(timezone.utc))
        self.assertIn("frequent_operations", self.analyze().reasons)

    def test_clients_have_separate_activity_and_recipient_history(self):
        for index in range(4):
            self.attempt(str(index))
        self.analyzer.record_successful_transfer("c1", "account")
        self.assertEqual(self.analyze(client_id="c2", recipient="account").reasons,
                         ("new_recipient",))
        self.assertEqual(self.analyze(recipient="account").reasons, ("frequent_operations",))

    def test_analysis_alone_does_not_register_attempts_or_success(self):
        for index in range(10):
            result = self.analyze(operation_id=str(index), recipient="account")
            self.assertEqual(result.reasons, ("new_recipient",))
        # Попытка перевода тоже не делает получателя знакомым.
        self.attempt("current")
        self.assertEqual(self.analyze(recipient="account").reasons, ("new_recipient",))
        self.analyzer.record_successful_transfer("c1", "account")
        self.assertEqual(self.analyze(recipient="account").reasons, ())
        self.assertEqual(self.analyze(recipient="another-account").reasons, ("new_recipient",))

    def test_external_recipient_uses_bank_and_account_together(self):
        recipient = ExternalRecipient("123", "bank-a")
        self.assertEqual(self.analyze(recipient=recipient).reasons, ("new_recipient",))
        self.analyzer.record_successful_transfer("c1", recipient)
        self.analyzer.record_successful_transfer("c1", recipient)
        self.assertEqual(self.analyze(recipient=ExternalRecipient("123", "bank-a")).reasons, ())
        for other in (ExternalRecipient("123", "bank-b"), ExternalRecipient("456", "bank-a"), "123"):
            with self.subTest(recipient=other):
                self.assertEqual(self.analyze(recipient=other).reasons, ("new_recipient",))

    def test_frequency_and_new_recipient_together_are_high_risk(self):
        for index in range(4):
            self.attempt(str(index))
        result = self.analyze(recipient="account")
        self.assertEqual(result.score, 3)
        self.assertIs(result.level, RiskLevel.HIGH)
        self.assertEqual(result.reasons, ("frequent_operations", "new_recipient"))

    def test_all_four_rules_are_combined(self):
        self.now = self.now.replace(hour=1)
        for index in range(4):
            self.attempt(str(index))
        result = self.analyzer.analyze(
            Decimal("100000"), Currency.RUB, occurred_at=self.now,
            client_id="c1", operation_id="current", recipient="account",
        )
        self.assertIs(result.level, RiskLevel.HIGH)
        self.assertEqual(result.score, 6)
        self.assertEqual(result.reasons, (
            "large_amount", "night_operation", "frequent_operations", "new_recipient",
        ))

    def test_custom_frequency_settings(self):
        self.analyzer = RiskAnalyzer(frequency_window=timedelta(minutes=10), frequency_limit=2)
        self.attempt("previous", seconds_ago=300)
        self.assertIn("frequent_operations", self.analyze().reasons)
        self.assertNotIn("frequent_operations", self.analyze(client_id="c2").reasons)

    def test_analyzers_have_independent_history(self):
        self.analyzer.record_successful_transfer("c1", "account")
        self.analyzer = RiskAnalyzer()
        self.assertEqual(self.analyze(recipient="account").reasons, ("new_recipient",))

    def test_invalid_context_and_history_inputs(self):
        for kwargs in (
            {"client_id": "c1"}, {"operation_id": "t1"}, {"recipient": "account"},
            {"client_id": "", "operation_id": "t1"},
            {"client_id": "c1", "operation_id": []},
            {"client_id": "c1", "operation_id": "t1", "recipient": 42},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(InvalidOperationError):
                self.analyzer.analyze(Decimal("1"), Currency.RUB, occurred_at=self.now, **kwargs)
        for client_id, operation_id, time in (
            (None, "t1", self.now), ("c1", " ", self.now), ("c1", "t1", datetime(2026, 11, 17)),
        ):
            with self.subTest(client_id=client_id, operation_id=operation_id):
                with self.assertRaises(InvalidOperationError):
                    self.analyzer.record_attempt(client_id, operation_id, occurred_at=time)
        for client_id, recipient in ((None, "a"), ("c1", " "), ("c1", None), ("c1", {})):
            with self.subTest(client_id=client_id, recipient=recipient):
                with self.assertRaises(InvalidOperationError):
                    self.analyzer.record_successful_transfer(client_id, recipient)

    def test_invalid_frequency_configuration(self):
        for kwargs in (
            {"frequency_limit": 0}, {"frequency_limit": -1}, {"frequency_limit": True},
            {"frequency_limit": 2.5}, {"frequency_window": timedelta(0)},
            {"frequency_window": timedelta(seconds=-1)}, {"frequency_window": 60},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(InvalidOperationError):
                RiskAnalyzer(**kwargs)


if __name__ == "__main__":
    unittest.main()

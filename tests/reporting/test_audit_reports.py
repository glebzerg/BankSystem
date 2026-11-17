"""Расчёты отчётов, исторические оценки и доступ через Bank."""

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bank.models.accounts import Currency
from bank.monitoring.audit import AuditLevel, AuditLog
from bank.reporting.audit_reports import AuditReports
from bank.bank import Bank
from bank.models.clients import Client
from bank.exceptions import AuthenticationError, HighRiskTransactionError, InvalidOperationError
from bank.monitoring.risk import RiskAssessment, RiskLevel
from bank.models.transactions import TransactionType


class AuditReportTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
        self.log = AuditLog(now=lambda: self.now)
        self.reports = AuditReports(self.log)

    def assessment(self, identifier, level, score, reasons=(), *, client_id="c1", blocked=False):
        return self.log.add(
            AuditLevel.ERROR if blocked else AuditLevel.INFO,
            "risk_blocked" if blocked else "risk_assessed", "Произвольное описание",
            client_id=client_id, transaction_id=identifier, risk=RiskAssessment(level, score, reasons),
        )

    def outcome(self, identifier, event):
        self.log.add(AuditLevel.INFO, event, "Результат", client_id="c1", transaction_id=identifier)

    def test_empty_profile_and_unassessed_events(self):
        self.outcome("cancelled", "transaction_cancelled")
        self.outcome("interest", "operation_completed")
        self.assertEqual(self.reports.suspicious_operations("c1"), [])
        self.assertEqual(self.reports.client_risk_profile("c1"), {
            "client_id": "c1", "total_operations": 0, "suspicious_operations": 0,
            "blocked_operations": 0, "by_level": {"low": 0, "medium": 0, "high": 0},
            "highest_risk": None, "reason_counts": {},
        })

    def test_profile_counts_distinct_operations_levels_reasons_and_blocks(self):
        self.assessment("low", RiskLevel.LOW, 0)
        self.assessment("medium", RiskLevel.MEDIUM, 1, ("new_recipient",))
        self.outcome("medium", "transaction_completed")
        self.assessment("high", RiskLevel.HIGH, 3, ("large_amount", "new_recipient"), blocked=True)
        self.outcome("high", "transaction_failed")
        self.assessment("foreign", RiskLevel.HIGH, 3, ("new_recipient",), client_id="c2")
        self.assertEqual([row["operation_id"] for row in self.reports.suspicious_operations("c1")],
                         ["medium", "high"])
        profile = self.reports.client_risk_profile("c1")
        self.assertEqual(profile["total_operations"], 3)
        self.assertEqual(profile["suspicious_operations"], 2)
        self.assertEqual(profile["blocked_operations"], 1)
        self.assertEqual(profile["by_level"], {"low": 1, "medium": 1, "high": 1})
        self.assertEqual(profile["reason_counts"], {"new_recipient": 2, "large_amount": 1})
        self.assertEqual(profile["highest_risk"], "high")
        blocked = self.reports.suspicious_operations("c1")[1]
        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["status"], "failed")

    def test_retry_preserves_peak_risk_and_does_not_double_count_operation(self):
        first = self.assessment("tx", RiskLevel.MEDIUM, 2, ("frequent_operations",))
        self.outcome("tx", "transaction_retry_scheduled")
        self.now += timedelta(minutes=1)
        last = self.assessment("tx", RiskLevel.LOW, 0)
        self.outcome("tx", "transaction_completed")
        rows = self.reports.suspicious_operations("c1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], {
            "operation_id": "tx", "first_assessed_at": first.occurred_at,
            "last_assessed_at": last.occurred_at, "risk_level": "medium", "score": 2,
            "reasons": ("frequent_operations",), "attempts": 2,
            "blocked": False, "status": "completed",
        })
        self.assertEqual(self.reports.client_risk_profile("c1")["total_operations"], 1)

    def test_period_has_inclusive_boundaries_and_does_not_show_future_outcome(self):
        first = self.assessment("tx", RiskLevel.MEDIUM, 2, ("frequent_operations",))
        self.outcome("tx", "transaction_retry_scheduled")
        self.now += timedelta(minutes=1)
        self.assessment("tx", RiskLevel.LOW, 0)
        self.outcome("tx", "transaction_completed")
        boundary = first.occurred_at.astimezone(Bank.BANK_TIMEZONE)
        rows = self.reports.suspicious_operations("c1", start_at=boundary, end_at=boundary)
        self.assertEqual(rows[0]["status"], "retry_pending")
        self.assertEqual(rows[0]["attempts"], 1)
        later = self.reports.client_risk_profile("c1", start_at=self.now)
        self.assertEqual(later["by_level"], {"low": 1, "medium": 0, "high": 0})
        self.assertEqual(self.reports.suspicious_operations("c1", start_at=self.now), [])

    def test_direct_operations_and_cancelled_retries_have_outcomes(self):
        self.assessment("direct:1", RiskLevel.MEDIUM, 2, ("large_amount",))
        self.outcome("direct:1", "operation_completed")
        self.assessment("retry", RiskLevel.MEDIUM, 1, ("new_recipient",))
        self.outcome("retry", "transaction_retry_scheduled")
        self.outcome("retry", "transaction_cancelled")
        rows = self.reports.suspicious_operations("c1")
        self.assertEqual([row["status"] for row in rows], ["completed", "cancelled"])

    def test_report_changes_do_not_mutate_history_or_future_reports(self):
        self.assessment("tx", RiskLevel.MEDIUM, 1, ("new_recipient",))
        rows = self.reports.suspicious_operations("c1")
        rows[0]["risk_level"] = "changed"
        rows.clear()
        profile = self.reports.client_risk_profile("c1")
        profile["by_level"]["medium"] = 999
        profile["reason_counts"].clear()
        self.assertEqual(self.reports.suspicious_operations("c1")[0]["risk_level"], "medium")
        self.assertEqual(self.reports.client_risk_profile("c1")["by_level"]["medium"], 1)
        self.assertEqual(self.reports.client_risk_profile("c1")["reason_counts"], {"new_recipient": 1})

    def test_invalid_arguments_are_rejected(self):
        with self.assertRaises(InvalidOperationError):
            AuditReports([])
        for method in (self.reports.suspicious_operations, self.reports.client_risk_profile):
            for client_id in (None, "", [], " "):
                with self.subTest(client_id=client_id), self.assertRaises(InvalidOperationError):
                    method(client_id)
            for kwargs in ({"start_at": datetime(2026, 11, 17)},
                           {"start_at": self.now, "end_at": self.now - timedelta(seconds=1)}):
                with self.subTest(kwargs=kwargs), self.assertRaises(InvalidOperationError):
                    method("c1", **kwargs)

    def test_risk_assessment_is_persisted_as_structured_json(self):
        risk = RiskAssessment(RiskLevel.HIGH, 3, ("large_amount", "new_recipient"))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            log = AuditLog(file_path=path)
            log.add(AuditLevel.ERROR, "risk_blocked", "Описание", risk=risk)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["risk"], {"level": "high", "score": 3,
                                              "reasons": ["large_amount", "new_recipient"]})

    def test_mutable_or_invalid_risk_data_is_rejected(self):
        with self.assertRaises(InvalidOperationError):
            self.log.add(AuditLevel.INFO, "event", "Описание", risk={})
        for level, score, reasons in (("high", 3, ()), (RiskLevel.HIGH, True, ()),
                                      (RiskLevel.HIGH, -1, ()), (RiskLevel.HIGH, 3, []),
                                      (RiskLevel.HIGH, 3, ("",))):
            with self.subTest(level=level, score=score, reasons=reasons):
                with self.assertRaises(InvalidOperationError):
                    RiskAssessment(level, score, reasons)


class BankAuditReportTests(unittest.TestCase):
    def setUp(self):
        hashing = patch.object(Bank, "PASSWORD_HASH_ITERATIONS", 1000)
        hashing.start()
        self.addCleanup(hashing.stop)
        self.now = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.bank = Bank(now=lambda: self.now)
        self.tokens = []
        self.accounts = []
        for name in ("Иван", "Анна"):
            client = Client(name, date(2000, 1, 1), {"phone": name})
            self.bank.add_client(client, "password")
            token = self.bank.authenticate_client(client.client_id, "password")
            self.tokens.append(token)
            self.accounts.append(self.bank.open_account(token, "standard", balance=Decimal("1000000"))["account_id"])

    def test_reports_use_authenticated_owner_and_include_direct_operations(self):
        token, other_token = self.tokens
        for _ in range(4):
            self.bank.deposit(token, self.accounts[0], Decimal("1"))
        with self.assertRaises(HighRiskTransactionError):
            self.bank.deposit(token, self.accounts[0], Decimal("100000"))
        rows = self.bank.get_suspicious_operations(token)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["operation_id"].startswith("direct:"))
        self.assertEqual(rows[0]["status"], "failed")
        self.assertEqual(self.bank.get_client_risk_profile(token)["total_operations"], 5)
        self.assertEqual(self.bank.get_suspicious_operations(other_token), [])
        self.assertEqual(self.bank.get_client_risk_profile(other_token)["total_operations"], 0)

    def test_queued_transfers_and_period_forwarding(self):
        token, other_token = self.tokens
        transaction = self.bank.create_transaction(
            token, TransactionType.INTERNAL_TRANSFER, Decimal("100000"), Currency.RUB,
            sender_account_id=self.accounts[0], recipient_account_id=self.accounts[1],
        )
        self.bank.process_transactions()
        rows = self.bank.get_suspicious_operations(token, start_at=self.now, end_at=self.now)
        self.assertEqual(rows[0]["operation_id"], transaction["transaction_id"])
        self.assertTrue(rows[0]["blocked"])
        self.assertEqual(self.bank.get_suspicious_operations(other_token), [])
        self.assertEqual(self.bank.get_client_risk_profile(token, start_at=self.now + timedelta(seconds=1))
                         ["total_operations"], 0)

    def test_invalid_sessions_cannot_read_reports(self):
        for method in (self.bank.get_suspicious_operations, self.bank.get_client_risk_profile):
            for token in (None, [], "invalid"):
                with self.subTest(token=token), self.assertRaises(AuthenticationError):
                    method(token)

    def test_blocked_client_cannot_read_reports(self):
        token = self.tokens[0]
        client_id = self.bank.get_client_risk_profile(token)["client_id"]
        for _ in range(3):
            with self.assertRaises(AuthenticationError):
                self.bank.authenticate_client(client_id, "wrong")
        for method in (self.bank.get_suspicious_operations, self.bank.get_client_risk_profile):
            with self.assertRaises(AuthenticationError):
                method(token)

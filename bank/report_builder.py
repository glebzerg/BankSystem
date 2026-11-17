"""Подготовка снимков отчётов, текстовое представление и экспорт."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .accounts import Currency
from .bank import Bank
from .exceptions import InvalidOperationError
from .transactions import TransactionStatus
from .report_text import format_text
from .report_export import export_to_csv, export_to_json


class ReportBuilder:
    """Собирает независимые снимки данных через публичные методы банка."""

    def __init__(self, bank: Bank, *, now: Callable[[], datetime] | None = None):
        if not isinstance(bank, Bank):
            raise InvalidOperationError("Ожидается объект Bank")
        if now is not None and not callable(now):
            raise InvalidOperationError("Источник времени должен быть вызываемым объектом")
        self._bank = bank
        self._now = now if now is not None else lambda: datetime.now(Bank.BANK_TIMEZONE)

    def _get_report_time(self) -> datetime:
        current = self._now()
        if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
            raise InvalidOperationError("Время отчёта должно содержать часовой пояс")
        return current.astimezone(Bank.BANK_TIMEZONE)

    @staticmethod
    def to_text(report: dict) -> str:
        """Текст готового отчёта без повторного чтения или изменения банка."""
        return format_text(report)

    @staticmethod
    def export_to_json(report: dict, file_path: str | Path) -> Path:
        """Сохранить весь снимок в JSON без потери точности денежных значений."""
        return export_to_json(report, file_path)

    @staticmethod
    def export_to_csv(report: dict, directory: str | Path) -> list[Path]:
        """Сохранить разделы снимка в отдельные CSV-таблицы."""
        return export_to_csv(report, directory)

    @staticmethod
    def build_charts(report: dict) -> dict:
        """Построить диаграммы снимка; matplotlib нужен только при вызове этого метода."""
        from .report_charts import build_charts

        return build_charts(report)

    @staticmethod
    def _build_summary(accounts: list[dict], transactions: list[dict]) -> dict:
        """Денежные итоги по валютам; комиссии только успешных поручений очереди."""
        balances = {currency.value: Decimal("0") for currency in Currency}
        totals = balances.copy()
        fees = balances.copy()
        statuses = {status.value: 0 for status in TransactionStatus}
        for account in accounts:
            if account["status"] == "closed":
                continue
            currency = account["currency"]
            balances[currency] += account["balance"]
            totals[currency] += account["balance"] + sum(account.get("portfolio", {}).values(), Decimal("0"))
        for transaction in transactions:
            statuses[transaction["status"]] += 1
            if transaction["status"] == TransactionStatus.COMPLETED.value:
                fees[transaction["currency"]] += transaction["fee"] or Decimal("0")
        return {
            "account_count": len(accounts),
            "transaction_count": len(transactions),
            "transactions_by_status": statuses,
            "balances_by_currency": balances,
            "total_by_currency": totals,
            "fees_by_currency": fees,
        }

    def build_client_report(self, session_token: str) -> dict:
        """Отчёт своего клиента за всю историю, доступную в памяти банка.

        transactions содержит поручения, созданные клиентом. Входящие переводы
        других клиентов видны как изменения баланса, без доступа к чужим поручениям.
        balance_history отражает свободные деньги, total_by_currency — также
        инвестиционный портфель. Закрытые счета сохраняются в списке и истории,
        но не включаются в текущие денежные итоги. Деньги остаются Decimal.
        """
        client = self._bank.get_client_info(session_token)
        generated_at = self._get_report_time()
        accounts = self._bank.search_accounts(session_token)
        transactions = self._bank.get_transactions(session_token)
        history = []
        for account in accounts:
            for point in self._bank.get_balance_history(session_token, account["account_id"]):
                history.append({
                    "account_id": point.account_id,
                    "occurred_at": point.occurred_at,
                    "balance": point.balance,
                    "currency": point.currency.value,
                    "operation_type": point.operation_type,
                    "operation_id": point.operation_id,
                })
        return {
            "report_type": "client",
            "generated_at": generated_at,
            "client": client,
            "summary": self._build_summary(accounts, transactions),
            "accounts": accounts,
            "transactions": transactions,
            "balance_history": history,
        }

    def build_bank_report(self) -> dict:
        """Служебный отчёт всего банка; суммы и рейтинги разных валют не смешиваются."""
        generated_at = self._get_report_time()
        snapshot = self._bank.get_bank_snapshot()
        summary = self._build_summary(snapshot["accounts"], snapshot["transactions"])
        summary["client_count"] = snapshot["client_count"]
        rankings = snapshot["client_balances_by_currency"]
        return {
            "report_type": "bank",
            "generated_at": generated_at,
            "summary": summary,
            "accounts": snapshot["accounts"],
            "transactions": snapshot["transactions"],
            "client_balances_by_currency": rankings,
            "top_three_by_currency": {
                currency: [row.copy() for row in rows[:3]] for currency, rows in rankings.items()
            },
        }

    def build_risk_report(
        self, session_token: str, *, start_at: datetime | None = None, end_at: datetime | None = None,
    ) -> dict:
        """Профиль, подозрительные операции и ошибки своего клиента за период.

        Расчёты выполняются готовыми методами Day5. В отчёте нет данных
        других клиентов; правила периода и учёта повторов сохранены.
        """
        client = self._bank.get_client_info(session_token)
        generated_at = self._get_report_time()
        period = {"start_at": start_at, "end_at": end_at}
        return {
            "report_type": "risk",
            "generated_at": generated_at,
            "client": client,
            "period": period,
            "summary": self._bank.get_client_risk_profile(session_token, **period),
            "suspicious_operations": self._bank.get_suspicious_operations(session_token, **period),
            "error_statistics": self._bank.get_error_statistics(session_token, **period),
        }

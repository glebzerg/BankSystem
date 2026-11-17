"""Подготовка данных отчётов; форматирование и экспорт добавляются отдельно."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from .accounts import Currency
from .bank import Bank
from .exceptions import InvalidOperationError
from .transactions import TransactionStatus


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
        balances = {currency.value: Decimal("0") for currency in Currency}
        totals = balances.copy()
        statuses = {status.value: 0 for status in TransactionStatus}
        for account in accounts:
            if account["status"] == "closed":
                continue
            currency = account["currency"]
            balances[currency] += account["balance"]
            totals[currency] += account["balance"] + sum(account.get("portfolio", {}).values(), Decimal("0"))
        for transaction in transactions:
            statuses[transaction["status"]] += 1

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
            "summary": {
                "account_count": len(accounts),
                "transaction_count": len(transactions),
                "transactions_by_status": statuses,
                "balances_by_currency": balances,
                "total_by_currency": totals,
            },
            "accounts": accounts,
            "transactions": transactions,
            "balance_history": history,
        }

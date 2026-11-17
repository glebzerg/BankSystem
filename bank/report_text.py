"""Текстовое представление готового снимка отчёта без обращения к банку."""

from datetime import datetime
from decimal import Decimal

from .exceptions import InvalidOperationError


_STATUSES = {
    "active": "Активен", "blocked": "Заблокирован", "frozen": "Заморожен", "closed": "Закрыт",
    "created": "Создана", "queued": "В очереди", "processing": "Выполняется",
    "completed": "Выполнена", "failed": "Отклонена", "cancelled": "Отменена",
    "retry_pending": "Ожидает повтора", "assessed": "Оценена",
}
_TYPES = {
    "BankAccount": "Обычный", "SavingsAccount": "Накопительный", "PremiumAccount": "Премиальный",
    "InvestmentAccount": "Инвестиционный", "deposit": "Пополнение", "withdrawal": "Снятие",
    "internal_transfer": "Внутренний перевод", "external_transfer": "Внешний перевод",
    "investment": "Инвестиция", "monthly_interest": "Проценты", "account_opened": "Открытие",
}
_RISKS = {"low": "Низкий", "medium": "Средний", "high": "Высокий", None: "Нет оценок"}
_REASONS = {
    "large_amount": "Крупная сумма", "night_operation": "Ночное время",
    "frequent_operations": "Частые операции", "new_recipient": "Новый получатель",
}


def _table(headers, rows) -> str:
    if not rows:
        return "Нет данных."
    # Перенос строки внутри значения не должен ломать таблицу.
    cells = [[str(value).replace("\r", " ").replace("\n", " ") for value in row]
             for row in [headers, *rows]]
    widths = [max(len(row[index]) for row in cells) for index in range(len(headers))]
    lines = [" | ".join(value.ljust(width) for value, width in zip(cells[0], widths))]
    lines.append("-+-".join("-" * width for width in widths))
    lines.extend(" | ".join(value.ljust(width) for value, width in zip(row, widths)) for row in cells[1:])
    return "\n".join(line.rstrip() for line in lines)


def _section(lines, title, headers, rows):
    lines.extend(["", title, _table(headers, rows)])


def _time(value: datetime) -> str:
    return value.isoformat(sep=" ", timespec="seconds")


def _financial_sections(report, lines):
    summary = report["summary"]
    if report["report_type"] == "bank":
        lines.append(f"Клиентов: {summary['client_count']}")
    lines.extend([f"Счетов: {summary['account_count']}", f"Поручений: {summary['transaction_count']}"])
    _section(lines, "Денежные итоги", ["Валюта", "Свободные деньги", "С портфелем", "Списанные комиссии поручений"], [
        [currency, f"{amount:.2f}", f"{summary['total_by_currency'][currency]:.2f}",
         f"{summary['fees_by_currency'][currency]:.2f}"]
        for currency, amount in summary["balances_by_currency"].items()
    ])
    _section(lines, "Статусы поручений", ["Статус", "Количество"], [
        [_STATUSES.get(status, status), count] for status, count in summary["transactions_by_status"].items()
    ])
    _section(lines, "Счета", ["Счёт", "Владелец", "Тип", "Баланс", "Портфель", "Валюта", "Статус"], [
        [account["account_id"], account["owner"], _TYPES.get(account["account_type"], account["account_type"]),
         f"{account['balance']:.2f}", f"{sum(account.get('portfolio', {}).values(), Decimal('0')):.2f}",
         account["currency"], _STATUSES.get(account["status"], account["status"])]
        for account in report["accounts"]
    ])
    _section(lines, "Поручения (комиссия — расчёт, списание только при успехе)",
             ["ID", "Тип", "Сумма", "Валюта", "Комиссия", "Статус"], [
        [transaction["transaction_id"], _TYPES.get(transaction["transaction_type"], transaction["transaction_type"]),
         f"{transaction['amount']:.2f}", transaction["currency"],
         "—" if transaction["fee"] is None else f"{transaction['fee']:.2f}",
         _STATUSES.get(transaction["status"], transaction["status"])]
        for transaction in report["transactions"]
    ])
    for transaction in report["transactions"]:
        if transaction["failure_reason"]:
            lines.append(f"Причина отказа {transaction['transaction_id']}: {transaction['failure_reason']}")
    if report["report_type"] == "client":
        _section(lines, "История свободного баланса", ["Счёт", "Время", "Баланс", "Валюта", "Операция"], [
            [point["account_id"], _time(point["occurred_at"]), f"{point['balance']:.2f}", point["currency"],
             _TYPES.get(point["operation_type"], point["operation_type"])] for point in report["balance_history"]
        ])
    else:
        _section(lines, "Топ-3 по каждой валюте", ["Валюта", "Место", "Клиент", "Сумма с портфелем"], [
            [currency, place, client["full_name"], f"{client['total_balance']:.2f}"]
            for currency, clients in report["top_three_by_currency"].items()
            for place, client in enumerate(clients, start=1)
        ])


def _risk_sections(report, lines):
    period = report["period"]
    start = _time(period["start_at"]) if period["start_at"] is not None else "без начала"
    end = _time(period["end_at"]) if period["end_at"] is not None else "без конца"
    summary = report["summary"]
    lines.extend([
        f"Период: {start} — {end} (границы включены)",
        f"Оценённых операций: {summary['total_operations']}",
        f"Подозрительных операций: {summary['suspicious_operations']}",
        f"Заблокировано по риску: {summary['blocked_operations']}",
        f"Максимальный риск: {_RISKS.get(summary['highest_risk'], summary['highest_risk'])}",
    ])
    _section(lines, "Уровни риска", ["Уровень", "Количество операций"], [
        [_RISKS.get(level, level), count] for level, count in summary["by_level"].items()
    ])
    _section(lines, "Причины максимальных оценок", ["Причина", "Количество операций"], [
        [_REASONS.get(reason, reason), count] for reason, count in summary["reason_counts"].items()
    ])
    _section(lines, "Подозрительные операции", ["ID", "Риск", "Баллы", "Попытки", "Блокировка", "Результат"], [
        [row["operation_id"], _RISKS.get(row["risk_level"], row["risk_level"]), row["score"], row["attempts"],
         "Да" if row["blocked"] else "Нет", _STATUSES.get(row["status"], row["status"])]
        for row in report["suspicious_operations"]
    ])
    for row in report["suspicious_operations"]:
        reasons = ", ".join(_REASONS.get(reason, reason) for reason in row["reasons"])
        lines.extend([f"Причины {row['operation_id']}: {reasons}",
                      f"  Первая оценка: {_time(row['first_assessed_at'])}; последняя: {_time(row['last_assessed_at'])}"])
    errors = report["error_statistics"]
    lines.extend(["", "Ошибки денежных операций",
                  f"Неудачных попыток: {errors['failed_attempts']}",
                  f"Окончательных отказов: {errors['failed_operations']}",
                  f"Блокировок по риску: {errors['risk_blocked_operations']}"])
    _section(lines, "Типы ошибок", ["Тип", "Количество"], list(errors["by_error_type"].items()))


def format_text(report: dict) -> str:
    """Отформатировать снимок ReportBuilder; исходные данные не изменяются."""
    titles = {"client": "Отчёт по клиенту", "bank": "Отчёт по банку", "risk": "Отчёт по рискам"}
    if not isinstance(report, dict) or not isinstance(report.get("report_type"), str):
        raise InvalidOperationError("Ожидается снимок отчёта ReportBuilder")
    report_type = report["report_type"]
    if report_type not in titles:
        raise InvalidOperationError("Неизвестный тип отчёта")
    generated = report.get("generated_at")
    if not isinstance(generated, datetime) or generated.tzinfo is None or generated.utcoffset() is None:
        raise InvalidOperationError("Дата отчёта должна содержать часовой пояс")
    try:
        lines = [titles[report_type], f"Сформирован: {_time(generated)}"]
        if report_type in ("client", "risk"):
            client = report["client"]
            lines.append(f"Клиент: {client['full_name']} | ID: {client['client_id']} | "
                         f"{_STATUSES.get(client['status'], client['status'])}")
        if report_type == "risk":
            _risk_sections(report, lines)
        else:
            _financial_sections(report, lines)
        return "\n".join(lines) + "\n"
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise InvalidOperationError("Некорректная структура снимка отчёта") from error

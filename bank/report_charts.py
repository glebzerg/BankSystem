"""Диаграммы по готовым снимкам отчётов без GUI и глобального состояния pyplot."""

import math
from datetime import datetime
from decimal import Decimal
from textwrap import fill

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from .exceptions import InvalidOperationError


_STATUSES = {
    "created": "Созданы", "queued": "В очереди", "processing": "Выполняются",
    "completed": "Выполнены", "failed": "Отклонены", "cancelled": "Отменены",
    "retry_pending": "Ожидают повтора",
}
_COLORS = {"completed": "#249978", "failed": "#d95656", "queued": "#e8b34b",
           "cancelled": "#8795a8", "processing": "#5086ca", "created": "#8f75b7",
           "retry_pending": "#cf874b"}


def _figure(title, report, height=5):
    figure = Figure(figsize=(9, height), dpi=120, layout="constrained")
    FigureCanvasAgg(figure)
    figure.suptitle(title, fontsize=15, fontweight="bold")
    axes = figure.subplots()
    axes.set_title(f"Сформирован: {report['generated_at'].isoformat(sep=' ', timespec='seconds')}",
                   fontsize=9, color="#586578", pad=16)
    return figure, axes


def _empty(axes, message):
    axes.text(0.5, 0.5, message, ha="center", va="center", transform=axes.transAxes,
              fontsize=14, color="#586578")
    axes.set_axis_off()


def _counts(values):
    if not isinstance(values, dict) or any(type(value) is not int or value < 0 for value in values.values()):
        raise InvalidOperationError("Количество операций должно быть целым числом >= 0")


def _amount(value):
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvalidOperationError("Баланс диаграммы должен быть конечным Decimal")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidOperationError("Баланс выходит за диапазон построения диаграммы")
    return number


def _status_pie(report):
    counts = report["summary"]["transactions_by_status"]
    _counts(counts)
    figure, axes = _figure("Статусы поручений", report)
    nonzero = [(status, count) for status, count in counts.items() if count]
    if not nonzero:
        _empty(axes, "Нет поручений")
        return figure
    wedges, _, _ = axes.pie(
        [count for _, count in nonzero], autopct="%1.1f%%", startangle=90,
        colors=[_COLORS.get(status, "#5086ca") for status, _ in nonzero],
        wedgeprops={"edgecolor": "white", "linewidth": 2}, textprops={"fontsize": 10},
    )
    axes.legend(wedges, [f"{_STATUSES.get(status, status)}: {count}" for status, count in nonzero],
                loc="center left", bbox_to_anchor=(1, 0.5), frameon=False)
    axes.set_aspect("equal")
    return figure


def _balance_bars(report, labels, amounts, title, currency):
    numbers = [_amount(value) for value in amounts]
    figure, axes = _figure(title, report, height=max(4.5, len(labels) * 0.55 + 2))
    if not labels:
        _empty(axes, "Нет счетов")
        return figure
    positions = list(range(len(labels)))
    bars = axes.barh(positions, numbers, color=["#d95656" if value < 0 else "#5086ca" for value in numbers])
    axes.set_yticks(positions, labels=[fill(label, width=28) for label in labels])
    axes.invert_yaxis()
    axes.bar_label(bars, labels=[f"{value:,.2f}".replace(",", " ") for value in amounts], padding=5, fontsize=9)
    axes.axvline(0, color="#8795a8", linewidth=1)
    axes.set_xlabel(currency)
    axes.grid(axis="x", alpha=0.2)
    axes.set_axisbelow(True)
    axes.margins(x=0.22)
    return figure


def _risk_bars(report):
    counts = report["summary"]["by_level"]
    _counts(counts)
    labels = {"low": "Низкий", "medium": "Средний", "high": "Высокий"}
    numbers = [counts[level] for level in labels]
    figure, axes = _figure("Распределение уровней риска", report)
    if not sum(numbers):
        _empty(axes, "Нет оценок риска")
        return figure
    bars = axes.bar(list(labels.values()), numbers, color=["#249978", "#e8b34b", "#d95656"], width=0.6)
    axes.bar_label(bars, padding=5)
    axes.set_ylabel("Количество операций")
    axes.yaxis.set_major_locator(MaxNLocator(integer=True))
    axes.set_ylim(0, max(numbers) + max(1, max(numbers) * 0.2))
    axes.grid(axis="y", alpha=0.2)
    axes.set_axisbelow(True)
    return figure


def build_charts(report: dict) -> dict[str, Figure]:
    """Создать фигуры без окон. Снимок не изменяется и банк не читается.

    Клиент: статусы и свободные балансы счетов. Банк: статусы и суммы клиентов
    с портфелями. Риски: распределение максимальных уровней за период.
    Каждая валюта получает свою диаграмму. Возвращённые фигуры принадлежат
    вызывающему коду; после использования их можно освободить через clear().
    """
    if not isinstance(report, dict) or report.get("report_type") not in ("client", "bank", "risk"):
        raise InvalidOperationError("Ожидается снимок отчёта ReportBuilder")
    generated = report.get("generated_at")
    if not isinstance(generated, datetime) or generated.tzinfo is None or generated.utcoffset() is None:
        raise InvalidOperationError("Дата отчёта должна содержать часовой пояс")
    charts = {}
    try:
        if report["report_type"] == "risk":
            charts["risk_distribution"] = _risk_bars(report)
            return charts
        charts["status_distribution"] = _status_pie(report)
        currencies = list(dict.fromkeys(account["currency"] for account in report["accounts"]))
        for currency in currencies:
            if report["report_type"] == "bank":
                rows = report["client_balances_by_currency"][currency]
                labels = [f"{row['full_name']} ({row['client_id'][-4:]})" for row in rows]
                amounts = [row["total_balance"] for row in rows]
                name, title = "client_balances", "Средства клиентов с портфелями"
            else:
                rows = [row for row in report["accounts"] if row["currency"] == currency]
                labels = [row["account_id"] for row in rows]
                amounts = [row["balance"] for row in rows]
                name, title = "account_balances", "Свободные балансы счетов"
            charts[f"{name}_{currency}"] = _balance_bars(report, labels, amounts, title, currency)
        if not currencies:
            charts["balances"] = _balance_bars(report, [], [], "Балансы", "")
        return charts
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperationError) as error:
        for figure in charts.values():
            figure.clear()
        if isinstance(error, InvalidOperationError):
            raise
        raise InvalidOperationError("Некорректная структура снимка отчёта") from error

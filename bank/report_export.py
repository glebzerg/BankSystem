"""Экспорт готовых снимков: точный JSON и отдельные плоские CSV-таблицы."""

import csv
import io
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

from .exceptions import InvalidOperationError, ReportExportError


_ACCOUNT_FIELDS = ["account_id", "owner", "account_type", "currency", "status", "balance",
                   "withdrawal_limit", "min_balance", "monthly_interest_rate", "overdraft_limit", "fixed_fee"]
_TRANSACTION_FIELDS = [
    "transaction_id", "transaction_type", "amount", "currency", "sender_account_id", "recipient_account_id",
    "external_recipient.account_number", "external_recipient.bank_id", "fee", "exchange_rate",
    "recipient_amount", "recipient_currency", "status", "failure_reason", "attempts_count",
    "created_at", "updated_at", "completed_at", "risk.level", "risk.score",
]
_HISTORY_FIELDS = ["account_id", "occurred_at", "balance", "currency", "operation_type", "operation_id"]
_SUSPICIOUS_FIELDS = ["operation_id", "first_assessed_at", "last_assessed_at", "risk_level", "score",
                      "attempts", "blocked", "status"]
_RANKING_FIELDS = ["position", "currency", "client_id", "full_name", "total_balance"]


def _scalar(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise InvalidOperationError("Денежное значение должно быть конечным")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidOperationError("Дата отчёта должна содержать часовой пояс")
        return value.isoformat()
    raise InvalidOperationError("Неподдерживаемый тип данных отчёта")


def _validate_report(report):
    if (not isinstance(report, dict) or report.get("report_type") not in ("client", "bank", "risk")
            or not isinstance(report.get("summary"), dict)
            or not isinstance(report.get("generated_at"), datetime)):
        raise InvalidOperationError("Ожидается снимок отчёта ReportBuilder")
    _scalar(report["generated_at"])


def _path(value):
    if not isinstance(value, (str, Path)) or (isinstance(value, str) and not value.strip()):
        raise InvalidOperationError("Путь должен быть непустой строкой или Path")
    return Path(value)


def _write_file(path: Path, text: str, encoding: str) -> None:
    """Заменить один файл после успешной записи во временный файл рядом с ним."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding=encoding, newline="", dir=path.parent,
                                prefix=".report-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        temporary.replace(path)
    except (OSError, ValueError, UnicodeError) as error:
        raise ReportExportError(f"Не удалось сохранить отчёт: {path}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Ошибка уборки не должна скрывать исходную причину отказа записи.
                pass


def export_to_json(report: dict, file_path: str | Path) -> Path:
    """Полный снимок в UTF-8; Decimal — строка, datetime — ISO 8601."""
    _validate_report(report)
    path = _path(file_path)
    try:
        text = json.dumps(report, ensure_ascii=False, indent=2, default=_scalar, allow_nan=False) + "\n"
    except (TypeError, ValueError) as error:
        raise InvalidOperationError("Данные отчёта не могут быть сохранены в JSON") from error
    _write_file(path, text, "utf-8")
    return path


def _flatten(mapping, prefix=""):
    """Вложенные словари сводки превращаются в строки metric/value."""
    rows = []
    for key, value in mapping.items():
        metric = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            rows.extend(_flatten(value, metric))
        else:
            rows.append({"metric": metric, "value": value})
    return rows


def _lookup(row, field):
    value = row
    for key in field.split("."):
        if value is None:
            return None
        value = value.get(key)
    return value


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int)):
        return value
    return _scalar(value)


def _csv_text(fields, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", lineterminator="\n")
    writer.writerow(fields)
    for row in rows:
        writer.writerow([_cell(_lookup(row, field)) for field in fields])
    return stream.getvalue()


def _tables(report):
    metadata = {key: report[key] for key in ("report_type", "generated_at")}
    for key in ("client", "period"):
        if key in report:
            metadata[key] = report[key]
    tables = {
        "metadata": (["metric", "value"], _flatten(metadata)),
        "summary": (["metric", "value"], _flatten(report["summary"])),
    }
    if report["report_type"] == "risk":
        operations = report["suspicious_operations"]
        tables["suspicious_operations"] = (_SUSPICIOUS_FIELDS, operations)
        tables["errors"] = (["metric", "value"], _flatten(report["error_statistics"]))
        reasons = [{"operation_id": row["operation_id"], "reason": reason}
                   for row in operations for reason in row["reasons"]]
    else:
        tables["accounts"] = (_ACCOUNT_FIELDS, report["accounts"])
        tables["portfolios"] = (["account_id", "asset_type", "amount"], [
            {"account_id": account["account_id"], "asset_type": asset, "amount": amount}
            for account in report["accounts"] for asset, amount in account.get("portfolio", {}).items()
        ])
        tables["transactions"] = (_TRANSACTION_FIELDS, report["transactions"])
        reasons = [{"operation_id": row["transaction_id"], "reason": reason}
                   for row in report["transactions"] for reason in (row["risk"]["reasons"] if row["risk"] else ())]
        if report["report_type"] == "client":
            tables["balance_history"] = (_HISTORY_FIELDS, report["balance_history"])
        else:
            for name, key in (("client_balances", "client_balances_by_currency"), ("top_three", "top_three_by_currency")):
                tables[name] = (_RANKING_FIELDS, [
                    {"position": place, **row} for ranking in report[key].values()
                    for place, row in enumerate(ranking, start=1)
                ])
    tables["risk_reasons"] = (["operation_id", "reason"], reasons)
    return tables


def export_to_csv(report: dict, directory: str | Path) -> list[Path]:
    """Отдельный CSV на раздел, UTF-8 с BOM, разделитель ;, пустые таблицы с заголовками.

    None сохраняется пустой ячейкой. Папки создаются автоматически. Все таблицы
    подготавливаются до записи; каждый файл заменяется отдельно. Для разных
    отчётов следует использовать разные папки: повторный экспорт заменяет файлы.
    """
    _validate_report(report)
    folder = _path(directory)
    try:
        prepared = {name: _csv_text(fields, rows) for name, (fields, rows) in _tables(report).items()}
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise InvalidOperationError("Некорректная структура снимка отчёта") from error
    paths = []
    for name, text in prepared.items():
        path = folder / f"{name}.csv"
        _write_file(path, text, "utf-8-sig")
        paths.append(path)
    return paths

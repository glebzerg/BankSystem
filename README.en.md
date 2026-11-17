# BankSystem

[Русский](README.md) | **English**

An educational banking system in Python: clients, four account types,
a transaction queue, auditing, risk analysis, and reports with charts.

The project demonstrates OOP through a connected example: abstract classes,
inheritance, polymorphism, encapsulation, and collaboration between modules.
No database or external banking services are required.

**Start with `examples.day6`** for a complete banking simulation.
Run `examples.day7` to save reports and charts.
The examples create their own clients and operations; no console input is needed.
The language links switch the documentation. Application messages and report labels
are currently in Russian.

## Contents

- [Quick start](#quick-start)
- [What to expect](#what-to-expect)
- [Other examples](#other-examples)
- [Using the bank in your code](#using-the-bank-in-your-code)
- [Available account types](#available-account-types)
- [Transactions and security](#transactions-and-security)
- [Reports and export](#reports-and-export)
- [Project structure](#project-structure)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Scope of the educational model](#scope-of-the-educational-model)

## Quick start

You need **Python 3.10 or newer**. The commands below are for PowerShell on Windows.
Open a terminal in the project root: the directory containing `README.md`,
`requirements.txt`, `bank`, and `examples`.

### 1. Create a virtual environment

```powershell
py -m venv .venv
```

Skip this step if `.venv` already exists.

### 2. Install dependencies

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`matplotlib` is required for charts and the full test suite. Text reports,
JSON, and CSV use the Python standard library.

### 3. Run the banking simulation

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m examples.day6
```

### 4. Save reports and charts

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m examples.day7
```

You do not need to activate the environment: these commands use its Python directly.
`-X utf8` enables Unicode output, and `-B` disables creation of new `__pycache__`
files. Run examples without `-O`, which disables their `assert` checks.

## What to expect

### Day6: see the system in action

The scenario creates **5 clients, 10 accounts, and 30 transactions**. It prints:

1. Clients, accounts, and opening balances.
2. Transactions entering the queue.
3. Execution results, risk levels, and rejection reasons.
4. One client's accounts, transaction history, and suspicious operations.
5. The top three clients, transaction statistics, and total bank balance.

Expected result: **25 successful operations, 5 rejections**, and a total balance
of **293,749.50 RUB**. Rejections are intentional: insufficient funds, a frozen
account, a withdrawal limit violation, and high risk.
The final message is `День 6: все проверки пройдены!` (Day 6: all checks passed).

### Day7: generate report files

This scenario creates a separate bank and generates a client report, a bank-wide
report, and a client risk report. The console prints the full output path:

```text
reports/day7/<run_timestamp>/
├── client/
│   ├── report.txt
│   ├── report.json
│   ├── csv/          # Accounts, transactions, history, and summary tables
│   └── charts/       # Diagrams and balance timelines
├── bank/
│   ├── report.txt
│   ├── report.json
│   ├── csv/
│   └── charts/
└── risk/
    ├── report.txt
    ├── report.json
    ├── csv/
    └── charts/
```

It saves **3 TXT, 3 JSON, 20 CSV, and 8 PNG files**.
Open `report.txt` to read a report, CSV files to inspect tables in Excel,
and PNG files to view charts. JSON contains the entire data snapshot.

Each Day7 run creates a new directory. Previous results are preserved;
generated Day7 files are excluded from Git.
Bank data is stored in memory, so every example starts a new scenario.
This is why Day6 and Day7 produce different totals.

## Other examples

Replace `examples.day6` in the run command with the module you want:

| Module | Demonstrates |
|---|---|
| `examples.day1` | Standard accounts, deposits, withdrawals, and freezing |
| `examples.day2` | Specialized accounts, inheritance, and polymorphism |
| `examples.day3` | Clients, authentication, account management, and bank analytics |
| `examples.day4` | Queues, priorities, scheduling, fees, conversion, and retries |
| `examples.day5` | Auditing, suspicious operations, blocking, and risk profiles |
| `examples.day6` | A complete banking simulation |
| `examples.day7` | Report generation and chart export |
| `examples.savings` | Detailed savings account checks |
| `examples.premium` | Premium account limits, fees, and overdraft |

Examples use a controlled clock where bank time restrictions apply.
Scheduled operations do not require waiting in real time.
Day5 also appends events to `audit_day5.jsonl` in the project root.

## Using the bank in your code

Create `demo.py` in the project root and paste this example:

```python
from datetime import date, datetime
from decimal import Decimal

from bank.bank import Bank
from bank.models.accounts import Currency
from bank.models.clients import Client
from bank.models.transactions import TransactionType

# A controlled clock lets this example run even during nighttime hours.
current = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
bank = Bank(now=lambda: current)

client = Client("Ivan Petrov", date(1990, 1, 1), {"email": "ivan@example.com"})
bank.add_client(client, "DemoPassword123")
token = bank.authenticate_client(client.client_id, "DemoPassword123")

main = bank.open_account(token, "standard", balance=Decimal("1000"))
savings = bank.open_account(token, "savings")

# Direct operations execute immediately.
bank.deposit(token, main["account_id"], Decimal("100"))
bank.withdraw(token, main["account_id"], Decimal("50"))

# Creating a transaction only adds it to the queue.
transaction = bank.create_transaction(
    token,
    TransactionType.INTERNAL_TRANSFER,
    Decimal("200"),
    Currency.RUB,
    sender_account_id=main["account_id"],
    recipient_account_id=savings["account_id"],
)
print("Before processing:", transaction["status"])  # queued

bank.process_transactions()
result = bank.get_transaction(token, transaction["transaction_id"])
print("After processing:", result["status"])  # completed

for account in bank.search_accounts(token):
    print(account["account_type"], account["balance"], account["currency"])
```

Run it with:

```powershell
.\.venv\Scripts\python.exe -X utf8 -B demo.py
```

The final balances are **850 RUB** in the standard account and **200 RUB**
in the savings account.

Important API conventions:

- Pass money as `Decimal("100.50")`, rather than `float`.
- `token` identifies a client session and is required for that client's accounts and transactions.
- `open_account()` returns a dictionary; the account number is in `account_id`.
- Returned dictionaries are snapshots. Request fresh data after an operation.
- `get_transactions(token)` shows transactions created by that client.
  Transfers initiated by other clients appear in the recipient's balance and balance history.
- `process_transactions()` processes ready transactions for the entire bank.
- Use operations through `Bank` to apply access checks, risk analysis, auditing, and balance history tracking.

Useful methods:

| Method | Purpose |
|---|---|
| `search_accounts(token)` | Own accounts, with optional account number, type, status, and currency filters |
| `freeze_account(token, account_id)` | Freeze an owned account |
| `unfreeze_account(token, account_id)` | Unfreeze an owned account |
| `close_account(token, account_id)` | Close an account with zero balance and an empty portfolio |
| `get_transactions(token)` | Own transactions and their current statuses |
| `cancel_transaction(token, transaction_id)` | Cancel an owned pending transaction |
| `get_balance_history(token, account_id)` | Immutable history of an owned account's available balance |
| `get_suspicious_operations(token)` | Own operations with medium or high risk |
| `get_error_statistics(token)` | Error statistics for own monetary operations |

## Available account types

Pass the account type as a string to `bank.open_account(token, account_type, ...)`.
The default currency is RUB. USD, EUR, KZT, and CNY are also supported.

| Type | Class | Features |
|---|---|---|
| `standard` | `BankAccount` | Single-withdrawal limit of 50,000 in the account currency |
| `savings` | `SavingsAccount` | Minimum balance, interest, and the standard withdrawal limit |
| `premium` | `PremiumAccount` | Default withdrawal limit of 100,000, overdraft of 10,000, and withdrawal fee of 50 |
| `investment` | `InvestmentAccount` | Available money and a separate virtual portfolio: `stocks`, `bonds`, `etf` |

Opening settings include `min_balance` and `monthly_interest_rate` for savings
accounts, and `withdrawal_limit`, `overdraft_limit`, and `fixed_fee` for premium
accounts. A rate of `Decimal("0.01")` means 1% per simulated month.
Premium withdrawal fees are charged in addition to the requested amount.

Apply interest with `bank.apply_monthly_interest(token, account_id)`.
Invest with `bank.invest(token, account_id, "stocks", Decimal("100"))`.
An investment reduces available money and increases the portfolio by the same amount.

## Transactions and security

Transactions support deposits, withdrawals, internal transfers, and simulated
external transfers. The main status flow is:

```text
created → queued → processing → completed
                              → failed
         → cancelled
```

Higher `priority` values execute first. `scheduled_at` specifies an execution
time and requires a timezone. A temporary error before funds are debited allows
a retry after 60 seconds, with at most three attempts in total.
Permanent failures are not automatically retried. The rejection reason is in
`failure_reason`; attempt errors are available through `get_transaction_errors(token)`.

Internal transfers are free. External transfers debit the amount plus a 1% fee.
Transfers between different bank account currencies require explicit rates in
`Bank(exchange_rates=...)`; see `examples.day4`.
External transfer fees and currency conversion amounts are rounded to two decimal places.

Before execution, the bank checks ownership, participant status, and operation risk:

| Indicator | Default condition | Points |
|---|---|---:|
| Large amount | At least 100,000 RUB, 1,000 USD/EUR, 500,000 KZT, or 10,000 CNY | 2 |
| Frequent operations | At least 5 distinct client operations in one minute, including the current one | 2 |
| New recipient | No previous successful transfer to that account | 1 |
| Nighttime | Between 00:00 and 05:00 Moscow time | 1 |

**Low risk: 0 points. Medium risk: 1–2. High risk: 3 or more.**
High risk blocks an operation before balances change.
Retrying the same transaction does not count as another distinct operation.
Configure thresholds with a `RiskAnalyzer` passed to `Bank(risk_analyzer=...)`.

The bank also has a separate nighttime restriction: from 00:00 inclusive to
05:00 exclusive, Moscow time, data changes are prohibited before risk assessment.
Authentication and reading remain available. Three consecutive incorrect passwords
block the client and revoke their sessions. Passwords are stored as salted hashes.

## Reports and export

Append this code to the `demo.py` example above:

```python
from bank.reporting.builder import ReportBuilder

builder = ReportBuilder(bank, now=lambda: current)
report = builder.build_client_report(token)

print(builder.to_text(report))
builder.export_to_json(report, "reports/day7/manual/client/report.json")
builder.export_to_csv(report, "reports/day7/manual/client/csv")
paths = builder.save_charts(report, "reports/day7/manual/client/charts")
print("Charts:", paths)

bank_report = builder.build_bank_report()
risk_report = builder.build_risk_report(token)
```

| Method | Result |
|---|---|
| `build_client_report(token)` | Own accounts, transactions, currency totals, and balance history |
| `build_bank_report()` | Administrative bank-wide summary, successful transaction fees, and top three clients per currency |
| `build_risk_report(token, start_at=..., end_at=...)` | Own risk profile, suspicious operations, and errors for a period |
| `to_text(report)` | A string with Russian tables for printing or writing to a file |
| `export_to_json(report, file_path)` | Complete JSON snapshot; returns the file path |
| `export_to_csv(report, directory)` | Separate CSV tables; returns a list of paths |
| `save_charts(report, directory)` | PNG charts; returns their paths and clears the figures |

Financial summaries keep currencies separate and distinguish available money
from totals including investment portfolios. Transaction counts and transaction
fee totals cover the queue. Direct deposits, withdrawals, and investments appear
in balance and risk history. Interest changes the balance without increasing
the frequency of client operations.

Charts include transaction status pie charts, balance and risk bar charts,
and step plots of actual balances. For a client report, pass `account_id` to
`save_charts()` to limit the balance timelines to one account.
Other charts still cover the full snapshot. No windows open automatically.

Output directories are created automatically. Repeating a manual export replaces
files with the same names; use separate directories for different reports.
JSON stores money as exact strings and dates as ISO 8601 timestamps with timezones.
CSV uses UTF-8 with a BOM, the `;` delimiter, and separate portfolio and risk-reason
tables. When reading CSV programmatically, specify `encoding="utf-8-sig"`
and `delimiter=";"`.

## Project structure

```text
BankSystem/
├── bank/
│   ├── bank.py          # Main entry point: clients, accounts, operations
│   ├── exceptions.py    # Banking exceptions
│   ├── models/          # Accounts, clients, transactions, balance points
│   ├── processing/      # Queue and transaction execution
│   ├── monitoring/      # Auditing and risk analysis
│   └── reporting/       # Report calculations, text, JSON/CSV, charts
├── examples/            # Day1–Day7 and individual account examples
├── tests/
│   ├── models/
│   ├── processing/
│   ├── monitoring/
│   ├── reporting/
│   └── integration/     # Collaboration between bank components
├── reports/             # Generated output
├── requirements.txt
├── README.en.md
└── README.md
```

Start exploring the implementation in `bank/models/accounts.py`, then read
`bank/bank.py`. See `examples/day6.py` for a complete usage scenario and
`examples/day7.py` for report generation.

## Tests

Run all automated checks:

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -t . -v
```

Run reporting and chart tests only:

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests/reporting -t . -v
```

The project currently has **233 tests**. A successful run ends with `OK`.
Tests cover account constraints, access control, transactions, risk, auditing,
history, export, and images. Run examples separately: `unittest` does not call
their `main()` functions.

## Troubleshooting

| Situation | What to do |
|---|---|
| `No module named bank` or `examples` | Change to the project root and run the example with `-m examples.day6` |
| `matplotlib` or `PIL` is missing | Install `requirements.txt` using the same `.venv` Python that runs the program |
| A nighttime restriction message appears | Pass `now` as shown in `demo.py`; the default bank clock uses real Moscow time |
| Day6 rejects some operations | These are intentional scenarios; check the final all-checks-passed message |
| A transaction remains `queued` | Call `process_transactions()`; a scheduled operation also needs to reach its execution time |
| Day6 produced no files | Run Day7, which saves results and prints their directory |
| A report did not change after a bank operation | Build a new snapshot using the appropriate report method |

Expected banking rejections use subclasses of `BankError`, such as
`InsufficientFundsError`, `AccountFrozenError`, `AuthenticationError`,
and `HighRiskTransactionError`. Catch them with `except BankError`, importing
it from `bank.exceptions`. `AuditWriteError` indicates an audit write failure;
`ReportExportError` indicates a report or PNG export failure.

## Scope of the educational model

- Clients, accounts, queues, and histories are stored in memory and disappear on exit.
  Exported reports and audit files remain; restoring the bank from them is not implemented.
- External transfers are simulated debits; no real payments or market data are requested.
- Interest is applied explicitly and portfolios are virtual. Selling assets is not implemented.
- The system is single-threaded. Sessions do not expire, and client unblocking is not implemented.
- `Bank(now=...)` controls bank restrictions, auditing, and balance history;
  `Transaction` timestamp fields currently use the system clock.
- Administrative analytics are available to the controlling Python code; employee roles are not implemented.
- Bank access checks, risk analysis, auditing, and history tracking apply to calls made through `Bank`.
- If auditing fails after a successful monetary operation, its result is preserved.
  An `AuditWriteError` in that case does not mean the deposit or debit was rolled back.
- Each JSON, CSV, and PNG file is replaced using a temporary file separately.
  Exporting the entire set is not a single atomic operation.

This project is intended for OOP practice and models banking rules for educational purposes.

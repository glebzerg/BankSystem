import hashlib
import hmac
import secrets
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .accounts import (
    AccountStatus,
    BankAccount,
    Currency,
    InvestmentAccount,
    PremiumAccount,
    SavingsAccount,
)
from .clients import Client, ClientStatus
from .audit import AuditLevel, AuditLog
from .exceptions import (
    AuthenticationError,
    ClientBlockedError,
    InvalidOperationError,
)
from .transaction_processor import TransactionProcessor
from .transaction_queue import TransactionQueue
from .transactions import ExternalRecipient, Transaction, TransactionType


class Bank:
    """Хранит клиентов, счета и учётные данные."""

    PASSWORD_HASH_ITERATIONS = 600_000
    BANK_TIMEZONE = timezone(timedelta(hours=3), name="MSK")

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        exchange_rates: Mapping[tuple[Currency, Currency], Decimal] | None = None,
        external_preflight: Callable[[Transaction], None] | None = None,
        audit_log: AuditLog | None = None,
    ):
        if now is not None and not callable(now):
            raise InvalidOperationError("Источник времени должен быть вызываемым объектом")
        if audit_log is not None and not isinstance(audit_log, AuditLog):
            raise InvalidOperationError("Ожидается журнал AuditLog")
        self._now = now if now is not None else lambda: datetime.now(self.BANK_TIMEZONE)
        self._audit_log = audit_log if audit_log is not None else AuditLog(now=self._get_current_time)
        self._clients: dict[str, Client] = {}
        self._accounts: dict[str, BankAccount] = {}
        self._account_owners: dict[str, str] = {}

        # ID клиента → (соль, хеш пароля).
        self._credentials: dict[str, tuple[bytes, bytes]] = {}

        # ID клиента → количество последовательных ошибок входа.
        self._failed_attempts: dict[str, int] = {}

        # Токен сессии → ID клиента.
        self._sessions: dict[str, str] = {}
        self._suspicious_events: list[dict] = []
        self._transactions: dict[str, Transaction] = {}
        self._transaction_owners: dict[str, str] = {}
        self._transaction_queue = TransactionQueue(now=self._get_current_time)
        self._transaction_processor = TransactionProcessor(
            self._transaction_queue, self._accounts,
            exchange_rates=exchange_rates,
            external_preflight=external_preflight,
            before_execute=self._validate_transaction_execution,
        )

    @staticmethod
    def _hash_password(password: str, salt: bytes) -> bytes:
        """Получить хеш пароля с использованием соли."""
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            Bank.PASSWORD_HASH_ITERATIONS,
        )

    def add_client(self, client: Client, password: str) -> None:
        """Зарегистрировать клиента и сохранить хеш его пароля."""
        self._ensure_operations_allowed()
        if not isinstance(client, Client):
            raise InvalidOperationError(
                "Ожидается объект Client"
            )

        if client.client_id in self._clients:
            raise InvalidOperationError(
                "Клиент с таким ID уже зарегистрирован"
            )

        if client.status is not ClientStatus.ACTIVE:
            raise InvalidOperationError(
                "При регистрации клиент должен быть активным"
            )

        if not isinstance(password, str) or not password.strip():
            raise InvalidOperationError(
                "Пароль должен быть непустой строкой"
            )

        salt = secrets.token_bytes(16)
        password_hash = self._hash_password(password, salt)

        self._clients[client.client_id] = client
        self._credentials[client.client_id] = (
            salt,
            password_hash,
        )

    def authenticate_client(self, client_id: str, password: str) -> str:
        """Проверить пароль и вернуть токен новой сессии."""
        if (
            not isinstance(client_id, str)
            or not client_id.strip()
            or not isinstance(password, str)
        ):
            self._record_failed_login(None)
            raise AuthenticationError(
                "ID и пароль должны быть строками, ID не должен быть пустым"
            )

        client = self._clients.get(client_id)

        if client is None:
            self._record_failed_login(None)
            raise AuthenticationError("Неверный ID клиента или пароль")

        if client.status is ClientStatus.BLOCKED:
            self._record_failed_login(client.client_id)
            raise ClientBlockedError("Доступ клиента заблокирован")

        salt, stored_hash = self._credentials[client_id]
        entered_hash = self._hash_password(password, salt)

        if not hmac.compare_digest(entered_hash, stored_hash):
            attempts = self._failed_attempts.get(client_id, 0) + 1
            self._failed_attempts[client_id] = attempts

            if attempts >= 3:
                client._status = ClientStatus.BLOCKED

                # Отозвать все ранее выданные клиенту сессии.
                self._sessions = {
                    token: owner_id
                    for token, owner_id in self._sessions.items()
                    if owner_id != client_id
                }

                self._record_failed_login(client_id)
                self._record_suspicious_activity(
                    "login_blocked", client_id,
                    "Клиент заблокирован после трёх неверных попыток входа",
                )

                raise ClientBlockedError(
                    "Доступ заблокирован после трёх неверных попыток"
                )

            self._record_failed_login(client_id)
            raise AuthenticationError("Неверный ID клиента или пароль")

        self._failed_attempts[client_id] = 0

        token = secrets.token_urlsafe(32)
        while token in self._sessions:
            token = secrets.token_urlsafe(32)

        self._sessions[token] = client_id
        return token

    def open_account(
        self,
        session_token: str,
        account_type: str,
        currency: Currency = Currency.RUB,
        balance: Decimal = Decimal("0"),
        **settings,
    ) -> dict:
        """Открыть счёт для клиента с действующей сессией."""
        client = self._get_authenticated_client(session_token)
        self._ensure_operations_allowed(client.client_id)

        account_classes = {
            "standard": BankAccount,
            "savings": SavingsAccount,
            "premium": PremiumAccount,
            "investment": InvestmentAccount,
        }
        if not isinstance(account_type, str) or account_type not in account_classes:
            raise InvalidOperationError(
                "Допустимые типы счетов: standard, savings, premium, investment"
            )

        allowed_settings = {
            "standard": set(),
            "savings": {"min_balance", "monthly_interest_rate"},
            "premium": {"withdrawal_limit", "overdraft_limit", "fixed_fee"},
            "investment": set(),
        }
        unknown_settings = set(settings) - allowed_settings[account_type]
        if unknown_settings:
            raise InvalidOperationError(
                "Недопустимые настройки счёта: " + ", ".join(sorted(unknown_settings))
            )

        account_class = account_classes[account_type]
        account = account_class(
            owner=client.full_name, currency=currency, balance=balance, **settings,
        )
        while account.account_id in self._accounts:
            account = account_class(
                owner=client.full_name, currency=currency, balance=balance, **settings,
            )

        client._add_account(account.account_id)
        self._accounts[account.account_id] = account
        self._account_owners[account.account_id] = client.client_id
        return account.get_account_info()

    def search_accounts(
        self,
        session_token: str,
        *,
        account_id: str | None = None,
        account_type: str | None = None,
        status: AccountStatus | None = None,
        currency: Currency | None = None,
    ) -> list[dict]:
        """Найти собственные счета по указанным фильтрам."""
        client = self._get_authenticated_client(session_token)
        account_classes = {
            "standard": BankAccount,
            "savings": SavingsAccount,
            "premium": PremiumAccount,
            "investment": InvestmentAccount,
        }
        if account_id is not None:
            if not isinstance(account_id, str) or not account_id.strip():
                raise InvalidOperationError("Номер счёта должен быть непустой строкой")
        if account_type is not None:
            if not isinstance(account_type, str) or account_type not in account_classes:
                raise InvalidOperationError(
                    "Допустимые типы счетов: standard, savings, premium, investment"
                )
        if status is not None and not isinstance(status, AccountStatus):
            raise InvalidOperationError("Статус должен быть элементом AccountStatus")
        if currency is not None and not isinstance(currency, Currency):
            raise InvalidOperationError("Валюта должна быть элементом Currency")

        result = []
        for number in client.account_numbers:
            account = self._accounts[number]
            if account_id is not None and number != account_id:
                continue
            if account_type is not None:
                if type(account) is not account_classes[account_type]:
                    continue
            if status is not None and account.status is not status:
                continue
            if currency is not None and account.currency is not currency:
                continue
            result.append(account.get_account_info())
        return result

    @staticmethod
    def _get_account_total(account: BankAccount) -> Decimal:
        """Посчитать средства счёта с учётом портфеля."""
        total = account.balance
        if isinstance(account, InvestmentAccount):
            total += sum(account.portfolio.values(), Decimal("0"))
        return total

    def get_total_balance(self) -> dict[str, Decimal]:
        """Посчитать средства банка отдельно по каждой валюте."""
        totals = {currency.value: Decimal("0") for currency in Currency}
        for account in self._accounts.values():
            if account.status is AccountStatus.CLOSED:
                continue
            totals[account.currency.value] += self._get_account_total(account)
        return totals

    def get_clients_ranking(
        self, currency: Currency = Currency.RUB,
    ) -> list[dict]:
        """Расположить клиентов по сумме средств в выбранной валюте."""
        if not isinstance(currency, Currency):
            raise InvalidOperationError("Валюта должна быть элементом Currency")
        client_totals = {client_id: Decimal("0") for client_id in self._clients}
        for account_id, account in self._accounts.items():
            if account.status is AccountStatus.CLOSED:
                continue
            if account.currency is not currency:
                continue
            client_id = self._account_owners[account_id]
            client_totals[client_id] += self._get_account_total(account)

        ranking = []
        for client_id, total in client_totals.items():
            client = self._clients[client_id]
            ranking.append({
                "client_id": client_id,
                "full_name": client.full_name,
                "total_balance": total,
                "currency": currency.value,
            })
        return sorted(
            ranking,
            key=lambda item: (-item["total_balance"], item["client_id"]),
        )

    def _get_owned_account(
        self, session_token: str, account_id: str,
    ) -> BankAccount:
        """Проверить сессию и принадлежность счёта клиенту."""
        client = self._get_authenticated_client(session_token)
        account = self._get_account(account_id)
        if self._account_owners.get(account_id) != client.client_id:
            self._record_suspicious_activity(
                "foreign_account_access", client.client_id,
                f"Попытка обращения к чужому счёту {account_id}",
            )
            raise InvalidOperationError("Счёт не принадлежит текущему клиенту")
        return account

    def freeze_account(self, session_token: str, account_id: str) -> None:
        """Заморозить активный счёт клиента."""
        account = self._get_owned_account(session_token, account_id)
        self._ensure_operations_allowed(self._account_owners[account.account_id])
        if account.status is not AccountStatus.ACTIVE:
            raise InvalidOperationError("Заморозить можно только активный счёт")
        account._status = AccountStatus.FROZEN

    def unfreeze_account(self, session_token: str, account_id: str) -> None:
        """Разморозить счёт клиента."""
        account = self._get_owned_account(session_token, account_id)
        self._ensure_operations_allowed(self._account_owners[account.account_id])
        if account.status is not AccountStatus.FROZEN:
            raise InvalidOperationError("Разморозить можно только замороженный счёт")
        account._status = AccountStatus.ACTIVE

    def close_account(self, session_token: str, account_id: str) -> None:
        """Закрыть счёт без денег, задолженности и вложений."""
        account = self._get_owned_account(session_token, account_id)
        self._ensure_operations_allowed(self._account_owners[account.account_id])
        if account.status is AccountStatus.CLOSED:
            raise InvalidOperationError("Счёт уже закрыт")
        if account.balance != Decimal("0"):
            raise InvalidOperationError("Для закрытия баланс должен быть равен нулю")
        if isinstance(account, InvestmentAccount):
            if any(amount != 0 for amount in account.portfolio.values()):
                raise InvalidOperationError("Нельзя закрыть счёт с инвестиционным портфелем")
        account._status = AccountStatus.CLOSED

    def _get_current_time(self) -> datetime:
        """Получить московское время из настроенного источника."""
        current = self._now()
        if (
            not isinstance(current, datetime)
            or current.tzinfo is None
            or current.utcoffset() is None
        ):
            raise InvalidOperationError(
                "Источник времени должен возвращать datetime с часовым поясом"
            )
        return current.astimezone(self.BANK_TIMEZONE)

    def _record_failed_login(self, client_id: str | None) -> None:
        """Записать отказ входа без пароля, токена и непроверенного ID."""
        self._audit_log.add(
            AuditLevel.WARNING, "login_failed", "Неудачная попытка входа",
            client_id=client_id, occurred_at=self._get_current_time(),
        )

    def _record_suspicious_activity(
        self, event_type: str, client_id: str | None, description: str,
        *, occurred_at: datetime | None = None,
    ) -> None:
        """Сохранить событие в прежнем журнале и в общем аудите."""
        current = occurred_at if occurred_at is not None else self._get_current_time()
        self._suspicious_events.append({
            "time": current,
            "event_type": event_type,
            "client_id": client_id,
            "description": description,
        })
        self._audit_log.add(
            AuditLevel.ERROR if event_type == "login_blocked" else AuditLevel.WARNING,
            event_type, description, client_id=client_id, occurred_at=current,
        )

    def _ensure_operations_allowed(self, client_id: str | None = None) -> None:
        """Запретить изменение данных с 00:00 до 05:00 по Москве."""
        current = self._get_current_time()
        if current.hour < 5:
            self._record_suspicious_activity(
                "night_operation", client_id,
                "Попытка изменения данных с 00:00 до 05:00",
                occurred_at=current,
            )
            raise InvalidOperationError("Операции запрещены с 00:00 до 05:00 по Москве")

    def get_suspicious_events(self, session_token: str) -> list[dict]:
        """Вернуть копии событий текущего клиента."""
        client = self._get_authenticated_client(session_token)
        return [
            event.copy() for event in self._suspicious_events
            if event["client_id"] == client.client_id
        ]

    def create_transaction(
        self,
        session_token: str,
        transaction_type: TransactionType,
        amount: Decimal,
        currency: Currency,
        *,
        sender_account_id: str | None = None,
        recipient_account_id: str | None = None,
        external_recipient: ExternalRecipient | None = None,
        priority: int = 0,
        scheduled_at: datetime | None = None,
    ) -> dict:
        """Проверить поручение клиента и сразу поставить его в очередь."""
        client = self._get_authenticated_client(session_token)
        self._ensure_operations_allowed(client.client_id)
        transaction = Transaction(
            transaction_type, amount, currency,
            sender_account_id=sender_account_id,
            recipient_account_id=recipient_account_id,
            external_recipient=external_recipient,
        )
        # Для пополнения клиент управляет получателем, для остальных — отправителем.
        controlled_id = (
            transaction.recipient_account_id
            if transaction_type is TransactionType.DEPOSIT
            else transaction.sender_account_id
        )
        account = self._get_owned_account(session_token, controlled_id)
        if account.currency is not currency:
            raise InvalidOperationError("Валюта поручения не совпадает с валютой счёта")
        if transaction.recipient_account_id is not None:
            self._get_account(transaction.recipient_account_id)
        self._transaction_queue.add(
            transaction, priority=priority, scheduled_at=scheduled_at,
        )
        self._transactions[transaction.transaction_id] = transaction
        self._transaction_owners[transaction.transaction_id] = client.client_id
        return self._transaction_info(transaction)

    def _validate_transaction_execution(self, transaction: Transaction) -> None:
        """Повторно проверить права и ограничения перед каждой попыткой."""
        owner_id = self._transaction_owners.get(transaction.transaction_id)
        if owner_id is None:
            raise InvalidOperationError("Владелец поручения не найден")
        self._ensure_operations_allowed(owner_id)
        owner = self._get_client(owner_id)
        if owner.status is ClientStatus.BLOCKED:
            raise ClientBlockedError("Создатель поручения заблокирован")
        controlled_id = (
            transaction.recipient_account_id
            if transaction.transaction_type is TransactionType.DEPOSIT
            else transaction.sender_account_id
        )
        if self._account_owners.get(controlled_id) != owner_id:
            raise InvalidOperationError("Счёт больше не принадлежит создателю поручения")
        for account_id in (transaction.sender_account_id, transaction.recipient_account_id):
            if account_id is None:
                continue
            self._get_account(account_id)
            participant = self._get_client(self._account_owners[account_id])
            if participant.status is ClientStatus.BLOCKED:
                raise ClientBlockedError("Клиент — участник операции заблокирован")

    def process_transactions(self) -> int:
        """Служебный запуск банка: обработать готовые операции всех клиентов.

        Это метод для управляющего кода учебной системы, не клиентская операция.
        Ночью очередь не извлекается. Возвращает число обработанных попыток.
        """
        self._ensure_operations_allowed()
        return len(self._transaction_processor.process_all())

    def _get_owned_transaction(self, session_token: str, transaction_id: str) -> Transaction:
        client = self._get_authenticated_client(session_token)
        if not isinstance(transaction_id, str) or not transaction_id.strip():
            raise InvalidOperationError("ID транзакции должен быть непустой строкой")
        transaction = self._transactions.get(transaction_id)
        if transaction is None:
            raise InvalidOperationError("Транзакция не найдена")
        if self._transaction_owners[transaction_id] != client.client_id:
            self._record_suspicious_activity(
                "foreign_transaction_access", client.client_id,
                f"Попытка обращения к чужому поручению {transaction_id}",
            )
            raise InvalidOperationError("Поручение не принадлежит текущему клиенту")
        return transaction

    def get_transaction(self, session_token: str, transaction_id: str) -> dict:
        """Вернуть снимок собственного поручения."""
        return self._transaction_info(self._get_owned_transaction(session_token, transaction_id))

    def get_transactions(self, session_token: str) -> list[dict]:
        """Вернуть собственные поручения в порядке создания."""
        client = self._get_authenticated_client(session_token)
        return [
            self._transaction_info(transaction)
            for transaction_id, transaction in self._transactions.items()
            if self._transaction_owners[transaction_id] == client.client_id
        ]

    def cancel_transaction(self, session_token: str, transaction_id: str) -> None:
        transaction = self._get_owned_transaction(session_token, transaction_id)
        self._ensure_operations_allowed(self._transaction_owners[transaction_id])
        self._transaction_queue.cancel(transaction.transaction_id)

    def get_transaction_errors(self, session_token: str) -> list[dict]:
        """Вернуть журнал только собственных поручений."""
        client = self._get_authenticated_client(session_token)
        return [
            {
                "transaction_id": record.transaction_id,
                "attempt": record.attempt,
                "occurred_at": record.occurred_at,
                "error_type": record.error_type,
                "message": record.message,
                "retry_at": record.retry_at,
            }
            for record in self._transaction_processor.errors
            if self._transaction_owners.get(record.transaction_id) == client.client_id
        ]

    @staticmethod
    def _transaction_info(transaction: Transaction) -> dict:
        external = transaction.external_recipient
        return {
            "transaction_id": transaction.transaction_id,
            "transaction_type": transaction.transaction_type.value,
            "amount": transaction.amount,
            "currency": transaction.currency.value,
            "sender_account_id": transaction.sender_account_id,
            "recipient_account_id": transaction.recipient_account_id,
            "external_recipient": None if external is None else {
                "account_number": external.account_number, "bank_id": external.bank_id,
            },
            "fee": transaction.fee,
            "exchange_rate": transaction.exchange_rate,
            "recipient_amount": transaction.recipient_amount,
            "recipient_currency": (
                transaction.recipient_currency.value if transaction.recipient_currency else None
            ),
            "status": transaction.status.value,
            "failure_reason": transaction.failure_reason,
            "attempts_count": transaction.attempts_count,
            "created_at": transaction.created_at,
            "updated_at": transaction.updated_at,
            "completed_at": transaction.completed_at,
        }

    def deposit(self, session_token: str, account_id: str, amount: Decimal) -> None:
        account = self._get_owned_account(session_token, account_id)
        self._ensure_operations_allowed(self._account_owners[account.account_id])
        account.deposit(amount)

    def withdraw(self, session_token: str, account_id: str, amount: Decimal) -> None:
        account = self._get_owned_account(session_token, account_id)
        self._ensure_operations_allowed(self._account_owners[account.account_id])
        account.withdraw(amount)

    def invest(
        self, session_token: str, account_id: str, asset_type: str, amount: Decimal,
    ) -> None:
        account = self._get_owned_account(session_token, account_id)
        self._ensure_operations_allowed(self._account_owners[account.account_id])
        if not isinstance(account, InvestmentAccount):
            raise InvalidOperationError(
                "Инвестирование доступно только инвестиционному счёту"
            )
        account.invest(asset_type, amount)

    def apply_monthly_interest(self, session_token: str, account_id: str) -> Decimal:
        account = self._get_owned_account(session_token, account_id)
        self._ensure_operations_allowed(self._account_owners[account.account_id])
        if not isinstance(account, SavingsAccount):
            raise InvalidOperationError(
                "Начисление процентов доступно только накопительному счёту"
            )
        return account.apply_monthly_interest()

    def _get_authenticated_client(self, session_token: str) -> Client:
        """Проверить сессию и текущий статус клиента."""
        if not isinstance(session_token, str) or not session_token:
            raise AuthenticationError("Необходимо передать токен сессии")

        client_id = self._sessions.get(session_token)
        if client_id is None:
            raise AuthenticationError("Сессия недействительна")

        client = self._get_client(client_id)
        if client.status is ClientStatus.BLOCKED:
            raise ClientBlockedError("Доступ клиента заблокирован")

        return client

    def _get_client(self, client_id: str) -> Client:
        """Найти клиента для внутренних операций банка."""
        if not isinstance(client_id, str) or not client_id.strip():
            raise InvalidOperationError(
                "ID клиента должен быть непустой строкой"
            )

        client = self._clients.get(client_id)

        if client is None:
            raise InvalidOperationError(
                "Клиент не найден"
            )

        return client

    def _get_account(self, account_id: str) -> BankAccount:
        """Найти счёт для внутренних операций банка."""
        if not isinstance(account_id, str) or not account_id.strip():
            raise InvalidOperationError(
                "Номер счёта должен быть непустой строкой"
            )

        account = self._accounts.get(account_id)

        if account is None:
            raise InvalidOperationError(
                "Счёт не найден"
            )

        return account

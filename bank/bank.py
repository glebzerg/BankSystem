import hashlib
import hmac
import secrets
from collections.abc import Callable
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
from .exceptions import (
    AuthenticationError,
    ClientBlockedError,
    InvalidOperationError,
)


class Bank:
    """Хранит клиентов, счета и учётные данные."""

    PASSWORD_HASH_ITERATIONS = 600_000
    BANK_TIMEZONE = timezone(timedelta(hours=3), name="MSK")

    def __init__(self, *, now: Callable[[], datetime] | None = None):
        if now is not None and not callable(now):
            raise InvalidOperationError("Источник времени должен быть вызываемым объектом")
        self._now = now if now is not None else lambda: datetime.now(self.BANK_TIMEZONE)
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
            raise AuthenticationError(
                "ID и пароль должны быть строками, ID не должен быть пустым"
            )

        client = self._clients.get(client_id)

        if client is None:
            raise AuthenticationError("Неверный ID клиента или пароль")

        if client.status is ClientStatus.BLOCKED:
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

                self._record_suspicious_activity(
                    "login_blocked", client_id,
                    "Клиент заблокирован после трёх неверных попыток входа",
                )

                raise ClientBlockedError(
                    "Доступ заблокирован после трёх неверных попыток"
                )

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

    def _record_suspicious_activity(
        self, event_type: str, client_id: str | None, description: str,
        *, occurred_at: datetime | None = None,
    ) -> None:
        """Сохранить подозрительное действие в памяти банка."""
        self._suspicious_events.append({
            "time": occurred_at if occurred_at is not None else self._get_current_time(),
            "event_type": event_type,
            "client_id": client_id,
            "description": description,
        })

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

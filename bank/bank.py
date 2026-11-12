import hashlib
import hmac
import secrets

from .accounts import BankAccount
from .clients import Client, ClientStatus
from .exceptions import (
    AuthenticationError,
    ClientBlockedError,
    InvalidOperationError,
)


class Bank:
    """Хранит клиентов, счета и учётные данные."""

    PASSWORD_HASH_ITERATIONS = 600_000

    def __init__(self):
        self._clients: dict[str, Client] = {}
        self._accounts: dict[str, BankAccount] = {}
        self._account_owners: dict[str, str] = {}

        # ID клиента → (соль, хеш пароля).
        self._credentials: dict[str, tuple[bytes, bytes]] = {}

        # ID клиента → количество последовательных ошибок входа.
        self._failed_attempts: dict[str, int] = {}

        # Токен сессии → ID клиента.
        self._sessions: dict[str, str] = {}

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

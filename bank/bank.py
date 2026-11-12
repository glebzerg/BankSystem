import hashlib
import secrets

from .accounts import BankAccount
from .clients import Client, ClientStatus
from .exceptions import InvalidOperationError


class Bank:
    """Хранит клиентов, счета и учётные данные."""

    PASSWORD_HASH_ITERATIONS = 600_000

    def __init__(self):
        self._clients: dict[str, Client] = {}
        self._accounts: dict[str, BankAccount] = {}
        self._account_owners: dict[str, str] = {}

        # ID клиента → (соль, хеш пароля).
        self._credentials: dict[str, tuple[bytes, bytes]] = {}

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
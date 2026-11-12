import uuid
from datetime import date
from enum import Enum

from .exceptions import InvalidOperationError


class ClientStatus(Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"


class Client:
    """Клиент банка с контактами и номерами счетов."""

    def __init__(
        self,
        full_name: str,
        birth_date: date,
        contacts: dict[str, str],
        client_id: str | None = None,
    ):
        if not isinstance(full_name, str) or not full_name.strip():
            raise InvalidOperationError(
                "ФИО должно быть непустой строкой"
            )

        # Принимаем именно date, без времени.
        if type(birth_date) is not date:
            raise InvalidOperationError(
                "Дата рождения должна иметь тип date"
            )

        today = date.today()

        if birth_date > today:
            raise InvalidOperationError(
                "Дата рождения не может быть в будущем"
            )

        age = today.year - birth_date.year

        if (today.month, today.day) < (
            birth_date.month,
            birth_date.day,
        ):
            age -= 1

        if age < 18:
            raise InvalidOperationError(
                "Клиент должен быть совершеннолетним"
            )

        if not isinstance(contacts, dict) or not contacts:
            raise InvalidOperationError(
                "Нужно указать хотя бы один контакт"
            )

        cleaned_contacts = {}

        for contact_type, value in contacts.items():
            if (
                not isinstance(contact_type, str)
                or not contact_type.strip()
                or not isinstance(value, str)
                or not value.strip()
            ):
                raise InvalidOperationError(
                    "Тип и значение контакта должны быть непустыми строками"
                )

            cleaned_contacts[contact_type.strip()] = value.strip()

        if client_id is None:
            client_id = self._generate_id()
        elif not isinstance(client_id, str) or not client_id.strip():
            raise InvalidOperationError(
                "ID клиента должен быть непустой строкой"
            )
        else:
            client_id = client_id.strip()

        self._client_id = client_id
        self._full_name = full_name.strip()
        self._birth_date = birth_date
        self._contacts = cleaned_contacts
        self._status = ClientStatus.ACTIVE
        self._account_numbers: list[str] = []

    @staticmethod
    def _generate_id() -> str:
        """Создать 12-значный ID на основе UUID."""
        number = uuid.uuid4().int % 10**12
        return f"{number:012d}"

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def full_name(self) -> str:
        return self._full_name

    @property
    def birth_date(self) -> date:
        return self._birth_date

    @property
    def contacts(self) -> dict[str, str]:
        return self._contacts.copy()

    @property
    def status(self) -> ClientStatus:
        return self._status

    @property
    def account_numbers(self) -> list[str]:
        return self._account_numbers.copy()

    def __str__(self) -> str:
        return (
            f"Client | ФИО: {self._full_name} | "
            f"ID: {self._client_id} | "
            f"Статус: {self._status.value} | "
            f"Количество счетов: {len(self._account_numbers)}"
        )
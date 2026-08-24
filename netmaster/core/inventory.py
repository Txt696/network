"""
Мост между хранилищем NetVault и подключениями NetMaster.

NetMaster не хранит собственную базу устройств: он читает те же
Markdown-заметки и берёт логины-пароли из зашифрованного secrets.enc.
"""

from netcore import Vault
from netcore.secretstore import LockedError


class Target:
    """Устройство + доступы, готовые для подключения."""

    def __init__(self, device, credentials=None):
        self.device = device
        self.credentials = credentials or {}

    # --- данные устройства
    @property
    def id(self):
        return self.device.id

    @property
    def name(self):
        return self.device.name

    @property
    def host(self):
        return self.device.target

    @property
    def port(self):
        return self.device.port

    @property
    def protocol(self):
        return self.device.protocol

    @property
    def vendor(self):
        return (self.device.vendor or "").lower()

    # --- доступы
    @property
    def username(self):
        return self.credentials.get("username", "")

    @property
    def password(self):
        return self.credentials.get("password", "")

    @property
    def enable_password(self):
        return self.credentials.get("enable_password", "")

    @property
    def key_file(self):
        return self.credentials.get("private_key_path", "")

    @property
    def key_passphrase(self):
        return self.credentials.get("key_passphrase", "")

    @property
    def has_credentials(self):
        return bool(self.username and (self.password or self.key_file))

    def describe(self):
        return "%s (%s:%s)" % (self.name, self.host or "нет адреса", self.port)

    def __repr__(self):
        return "<Target %s %s>" % (self.id, self.host)


class Inventory:
    """Список устройств из хранилища с подстановкой доступов."""

    def __init__(self, vault):
        self.vault = vault if isinstance(vault, Vault) else Vault(vault)

    @property
    def is_locked(self):
        return self.vault.is_locked

    def unlock(self, password):
        self.vault.unlock(password)
        return self

    def devices(self, query="", kind=None, site=None, tag=None, status=None):
        return self.vault.search(query, kind=kind, site=site, tag=tag, status=status)

    def credentials(self, device):
        """Доступы устройства. Требует открытого хранилища."""
        if self.vault.is_locked:
            raise LockedError("Хранилище закрыто — введите мастер-пароль")
        return self.vault.secrets.get(device.secret_ref) or {}

    def target(self, device_or_id):
        """Собрать Target по устройству или его id."""
        device = device_or_id
        if isinstance(device_or_id, str):
            device = self.vault.get(device_or_id)
            if device is None:
                raise KeyError("Устройство не найдено: %s" % device_or_id)
        return Target(device, self.credentials(device))

    def targets(self, devices=None, **filters):
        """Targets для набора устройств (по умолчанию — по фильтрам поиска)."""
        items = devices if devices is not None else self.devices(**filters)
        return [self.target(device) for device in items]

    def missing_credentials(self, targets):
        """Устройства, к которым не получится подключиться."""
        return [t for t in targets if not t.has_credentials or not t.host]

    def save_output(self, device_id, title, text):
        """Сохранить вывод команды в хранилище рядом с заметкой устройства."""
        return self.vault.add_note(device_id, title, text)

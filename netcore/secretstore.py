"""
Зашифрованное хранилище доступов (secrets.enc).

Секреты адресуются строковой ссылкой (ref), например "core-sw-01/admin".
В Markdown-заметке устройства хранится только ссылка, сами пароли —
здесь, в файле, зашифрованном мастер-паролем.
"""

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import crypto

FORMAT = "netvault-secrets"
VERSION = 1
AAD = b"netvault-secrets-v1"

# Поля одной записи. Пустые значения не сохраняются.
FIELDS = (
    "username",
    "password",
    "enable_password",
    "snmp_community",
    "private_key_path",
    "key_passphrase",
    "api_token",
    "notes",
)


class LockedError(Exception):
    """Хранилище закрыто — нужен мастер-пароль."""


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write(path, text):
    """Запись через временный файл, чтобы не потерять данные при сбое."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".enc")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class SecretStore:
    """Файл с доступами. По умолчанию закрыт; unlock() открывает его в памяти."""

    def __init__(self, path):
        self.path = Path(path)
        self._dek = None
        self._data = None
        self._header = None
        self._last_use = 0.0

    # ---------------------------------------------------------------- создание
    @classmethod
    def create(cls, path, password, kdf_name=None):
        """Создать новое пустое хранилище с мастер-паролем."""
        path = Path(path)
        if path.exists():
            raise FileExistsError("Хранилище уже существует: %s" % path)
        store = cls(path)
        kdf_name = kdf_name or crypto.default_kdf()
        params = crypto.default_params(kdf_name)
        salt = crypto.new_salt()
        kek = crypto.derive_key(password, salt, kdf_name, params)
        dek = crypto.new_key()
        store._header = {
            "format": FORMAT,
            "version": VERSION,
            "kdf": {"name": kdf_name, "salt": crypto.b64e(salt), "params": params},
            "wrapped_key": crypto.encrypt(dek, kek, AAD),
        }
        store._dek = dek
        store._data = {"secrets": {}, "created": _now()}
        store._save()
        store._touch()
        return store

    # ------------------------------------------------------------ блокировка
    @property
    def is_locked(self):
        return self._dek is None

    @property
    def exists(self):
        return self.path.exists()

    def unlock(self, password):
        """Открыть хранилище мастер-паролем."""
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if raw.get("format") != FORMAT:
            raise ValueError("Это не файл секретов NetVault: %s" % self.path)
        kdf = raw["kdf"]
        kek = crypto.derive_key(
            password, crypto.b64d(kdf["salt"]), kdf["name"], kdf.get("params")
        )
        dek = crypto.decrypt(raw["wrapped_key"], kek, AAD)
        payload = crypto.decrypt(raw["payload"], dek, AAD) if raw.get("payload") else b"{}"
        self._header = {k: raw[k] for k in ("format", "version", "kdf", "wrapped_key")}
        self._dek = dek
        self._data = json.loads(payload.decode("utf-8")) or {"secrets": {}}
        self._data.setdefault("secrets", {})
        self._touch()
        return self

    def lock(self):
        """Забыть ключ и расшифрованные данные."""
        self._dek = None
        self._data = None
        self._last_use = 0.0

    def maybe_autolock(self, timeout_seconds):
        """Закрыть хранилище, если им не пользовались timeout_seconds. True — закрыли."""
        if self.is_locked or not timeout_seconds:
            return False
        if time.monotonic() - self._last_use >= timeout_seconds:
            self.lock()
            return True
        return False

    def seconds_idle(self):
        return 0 if self.is_locked else time.monotonic() - self._last_use

    def _touch(self):
        self._last_use = time.monotonic()

    def _require_open(self):
        if self.is_locked:
            raise LockedError("Хранилище закрыто — введите мастер-пароль")
        self._touch()

    # ---------------------------------------------------------------- доступ
    def refs(self):
        self._require_open()
        return sorted(self._data["secrets"])

    def get(self, ref):
        """Вернуть копию записи или None."""
        self._require_open()
        record = self._data["secrets"].get(ref)
        return dict(record) if record else None

    def put(self, ref, **fields):
        """Создать или обновить запись. Пустые значения удаляют поле."""
        self._require_open()
        unknown = set(fields) - set(FIELDS)
        if unknown:
            raise ValueError("Неизвестные поля секрета: %s" % ", ".join(sorted(unknown)))
        record = self._data["secrets"].get(ref, {})
        for key, value in fields.items():
            if value in (None, ""):
                record.pop(key, None)
            else:
                record[key] = value
        record["updated"] = _now()
        self._data["secrets"][ref] = record
        self._save()
        return dict(record)

    def delete(self, ref):
        self._require_open()
        existed = self._data["secrets"].pop(ref, None) is not None
        if existed:
            self._save()
        return existed

    def rename(self, old_ref, new_ref):
        self._require_open()
        if old_ref not in self._data["secrets"]:
            return False
        self._data["secrets"][new_ref] = self._data["secrets"].pop(old_ref)
        self._save()
        return True

    def change_password(self, old_password, new_password, kdf_name=None):
        """Сменить мастер-пароль (перешифровывается только ключ данных)."""
        if self.is_locked:
            self.unlock(old_password)
        else:
            # проверяем старый пароль, чтобы им нельзя было пренебречь
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            kdf = raw["kdf"]
            kek = crypto.derive_key(
                old_password, crypto.b64d(kdf["salt"]), kdf["name"], kdf.get("params")
            )
            crypto.decrypt(raw["wrapped_key"], kek, AAD)
        kdf_name = kdf_name or crypto.default_kdf()
        params = crypto.default_params(kdf_name)
        salt = crypto.new_salt()
        new_kek = crypto.derive_key(new_password, salt, kdf_name, params)
        self._header["kdf"] = {"name": kdf_name, "salt": crypto.b64e(salt), "params": params}
        self._header["wrapped_key"] = crypto.encrypt(self._dek, new_kek, AAD)
        self._save()

    def export_plain(self):
        """Все секреты в открытом виде — только для резервной копии пользователем."""
        self._require_open()
        return json.loads(json.dumps(self._data["secrets"]))

    # ----------------------------------------------------------------- запись
    def _save(self):
        if self._dek is None:
            raise LockedError("Нечего сохранять: хранилище закрыто")
        self._data["updated"] = _now()
        payload = json.dumps(self._data, ensure_ascii=False).encode("utf-8")
        blob = dict(self._header)
        blob["payload"] = crypto.encrypt(payload, self._dek, AAD)
        blob["updated"] = self._data["updated"]
        _atomic_write(self.path, json.dumps(blob, ensure_ascii=False, indent=2))
        try:
            os.chmod(self.path, 0o600)
        except OSError:  # pragma: no cover - Windows/FS без POSIX-прав
            pass

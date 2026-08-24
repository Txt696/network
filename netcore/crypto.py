"""
Криптографический слой NetVault.

Схема:
  мастер-пароль --KDF--> KEK (key encryption key)
  KEK --AES-256-GCM--> шифрует случайный DEK (data encryption key)
  DEK --AES-256-GCM--> шифрует полезную нагрузку (все секреты)

Такая двухуровневая схема позволяет менять мастер-пароль,
перешифровав только DEK, а не весь файл секретов.

KDF: Argon2id, если установлен argon2-cffi, иначе scrypt из cryptography.
Имя KDF пишется в заголовок файла, поэтому хранилище остаётся читаемым
на машине с другим набором библиотек (если KDF там доступен).
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

try:  # опционально: более стойкий KDF
    import argon2.low_level as _argon2
    HAS_ARGON2 = True
except ImportError:  # pragma: no cover - зависит от окружения
    _argon2 = None
    HAS_ARGON2 = False

KDF_SCRYPT = "scrypt"
KDF_ARGON2ID = "argon2id"

SCRYPT_PARAMS = {"n": 2 ** 15, "r": 8, "p": 1}
ARGON2_PARAMS = {"time_cost": 3, "memory_cost": 65536, "parallelism": 4}

KEY_LEN = 32
SALT_LEN = 16
NONCE_LEN = 12


class CryptoError(Exception):
    """Ошибка шифрования или расшифровки (в т.ч. неверный пароль)."""


def default_kdf():
    """Имя KDF, который будет использован для новых хранилищ."""
    return KDF_ARGON2ID if HAS_ARGON2 else KDF_SCRYPT


def default_params(kdf_name):
    return dict(ARGON2_PARAMS if kdf_name == KDF_ARGON2ID else SCRYPT_PARAMS)


def b64e(data):
    return base64.b64encode(data).decode("ascii")


def b64d(text):
    return base64.b64decode(text.encode("ascii"))


def new_salt():
    return os.urandom(SALT_LEN)


def new_key():
    return os.urandom(KEY_LEN)


def derive_key(password, salt, kdf_name=None, params=None):
    """Получить ключ из мастер-пароля."""
    if isinstance(password, str):
        password = password.encode("utf-8")
    kdf_name = kdf_name or default_kdf()
    params = params or default_params(kdf_name)

    if kdf_name == KDF_ARGON2ID:
        if not HAS_ARGON2:
            raise CryptoError(
                "Хранилище создано с Argon2id, установите пакет argon2-cffi: "
                "pip install argon2-cffi"
            )
        return _argon2.hash_secret_raw(
            secret=password,
            salt=salt,
            time_cost=int(params["time_cost"]),
            memory_cost=int(params["memory_cost"]),
            parallelism=int(params["parallelism"]),
            hash_len=KEY_LEN,
            type=_argon2.Type.ID,
        )
    if kdf_name == KDF_SCRYPT:
        kdf = Scrypt(
            salt=salt,
            length=KEY_LEN,
            n=int(params["n"]),
            r=int(params["r"]),
            p=int(params["p"]),
        )
        return kdf.derive(password)
    raise CryptoError("Неизвестный KDF: %s" % kdf_name)


def encrypt(plaintext, key, aad=b""):
    """Зашифровать байты. Возвращает dict с nonce и ciphertext в base64."""
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    nonce = os.urandom(NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return {"nonce": b64e(nonce), "ct": b64e(ct)}


def decrypt(box, key, aad=b""):
    """Расшифровать dict из encrypt(). Кидает CryptoError при неверном ключе."""
    try:
        return AESGCM(key).decrypt(b64d(box["nonce"]), b64d(box["ct"]), aad)
    except (KeyError, TypeError, ValueError) as exc:
        raise CryptoError("Повреждённые данные хранилища") from exc
    except Exception as exc:  # InvalidTag и подобное
        raise CryptoError("Неверный мастер-пароль или файл повреждён") from exc

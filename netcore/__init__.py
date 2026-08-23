"""
netcore — общее ядро NetVault (хранилище данных) и NetMaster (управление).

Здесь модель устройства, работа с Markdown-заметками и шифрованное
хранилище доступов. Модули ядра не зависят от графического интерфейса.
"""

from .models import Device, KINDS, PROTOCOLS, STATUSES
from .secretstore import SecretStore, LockedError
from .vault import Vault, VaultError
from .crypto import CryptoError

__all__ = [
    "Device", "KINDS", "PROTOCOLS", "STATUSES",
    "SecretStore", "LockedError", "Vault", "VaultError", "CryptoError",
]
__version__ = "1.0.0"

"""
Сохранённые наборы команд — чтобы не набирать одно и то же каждый раз.

Список лежит в самом хранилище (`.netvault/macros.json`), поэтому едет
вместе с ним на другую машину. Один и тот же список используют панель
кнопок над терминалом и диалог массовых команд.
"""

import json

MACROS_FILE = "macros.json"

# Встроенные наборы. vendor="" — показывать для любого устройства.
DEFAULTS = [
    {"name": "Версия и инвентарь", "vendor": "cisco",
     "commands": ["show version", "show inventory"]},
    {"name": "Конфигурация", "vendor": "cisco",
     "commands": ["show running-config"]},
    {"name": "Интерфейсы", "vendor": "cisco",
     "commands": ["show ip interface brief", "show interfaces status"]},
    {"name": "Соседи", "vendor": "cisco",
     "commands": ["show cdp neighbors detail"]},
    {"name": "VLAN и MAC", "vendor": "cisco",
     "commands": ["show vlan brief", "show mac address-table"]},
    {"name": "Версия", "vendor": "huawei",
     "commands": ["display version"]},
    {"name": "Конфигурация", "vendor": "huawei",
     "commands": ["display current-configuration"]},
    {"name": "Интерфейсы", "vendor": "huawei",
     "commands": ["display interface brief"]},
    {"name": "Соседи", "vendor": "huawei",
     "commands": ["display lldp neighbor brief"]},
    {"name": "Версия", "vendor": "mikrotik",
     "commands": ["/system resource print"]},
    {"name": "Конфигурация", "vendor": "mikrotik",
     "commands": ["/export"]},
    {"name": "Интерфейсы", "vendor": "mikrotik",
     "commands": ["/interface print"]},
    {"name": "Состояние", "vendor": "",
     "commands": ["uname -a", "uptime", "df -h", "free -m"]},
]


def _path(vault):
    from netcore.vault import META_DIR

    return vault.path / META_DIR / MACROS_FILE


def _clean(items):
    """Отбросить мусор: без имени или без команд макрос бесполезен."""
    result = []
    for item in items or []:
        name = str(item.get("name", "")).strip()
        commands = [str(c).strip() for c in item.get("commands", []) if str(c).strip()]
        if name and commands:
            result.append({"name": name,
                           "vendor": str(item.get("vendor", "")).strip().lower(),
                           "commands": commands})
    return result


def load(vault=None):
    """Список макросов: свой из хранилища, иначе встроенный."""
    if vault is None:
        return _clean(DEFAULTS)
    try:
        items = json.loads(_path(vault).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _clean(DEFAULTS)
    return _clean(items)


def save(vault, items):
    """Записать список в хранилище. Пустой список вернёт встроенные наборы."""
    from netcore.vault import _atomic_write_text

    items = _clean(items)
    path = _path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, json.dumps(items, ensure_ascii=False, indent=2))
    return items


def for_vendor(items, vendor):
    """Макросы, подходящие устройству: свои вендорские плюс общие."""
    vendor = (vendor or "").lower()
    return [item for item in items
            if not item["vendor"] or item["vendor"] in vendor]


def as_text(item):
    """Команды макроса одной строкой на команду — для текстовых полей."""
    return "\n".join(item["commands"])

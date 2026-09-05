"""
Порты устройства: сколько их и как они называются.

Набор портов пишется в заметке устройства группами — так же коротко,
как их называет сам вендор:

    ports:
      - Gi1/0/1-48       # 48 гигабитных
      - Te1/0/49-52      # четыре десятки
      - ge-0/0/0-47      # Juniper

Из групп разворачивается список имён (Gi1/0/1 … Gi1/0/48), по которому
в NetVault каждый порт настраивается отдельно. Сама настройка лежит там же,
где и раньше — в полях `uplinks` и `vlans`:

    uplinks:
      - "Gi1/0/48 -> balkan-sw-01:Gi1/0/1"
    vlans:
      - 10                        # VLAN устройства, без привязки к порту
      - "Gi1/0/1: access 10"      # порт в access-VLAN 10
      - "Gi1/0/48: trunk 10 20"   # транк с разрешёнными VLAN

VLAN в записи порта разделяются пробелом, а не запятой: запятая — разделитель
элементов списка, из-за неё строка развалилась бы при чтении заметки.
"""

import re

# Сокращения портов, которые печатает сам свитч. (код, расшифровка)
PORT_TYPES = (
    ("Fa", "FastEthernet, 100 Мбит"),
    ("Gi", "GigabitEthernet, 1 Гбит"),
    ("Te", "TenGigabitEthernet, 10 Гбит"),
    ("Twe", "TwentyFiveGigE, 25 Гбит"),
    ("Fo", "FortyGigE, 40 Гбит"),
    ("Hu", "HundredGigE, 100 Гбит"),
    ("Eth", "Ethernet"),
    ("ge-", "Juniper, 1 Гбит"),
    ("xe-", "Juniper, 10 Гбит"),
    ("et-", "Juniper, 100 Гбит"),
    ("Po", "Port-channel / агрегат"),
    ("mgmt", "Порт управления"),
)

VLAN_MODES = ("access", "trunk")

# Больше портов, чем бывает в одном шасси, — защита от опечатки вроде Gi1/0/1-99999.
MAX_PORTS = 4096

_GROUP = re.compile(r"^([A-Za-z]+-?)((?:\d+/)*)(\d+)(?:\s*-\s*(\d+))?$")
_PORT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9/-]*\d$")
_ENTRY = re.compile(r"^([^:]+):(.*)$")


# --------------------------------------------------------------- группы портов
def parse_group(spec):
    """`Gi1/0/1-48` -> ('Gi', '1/0/', 1, 48). None — строка не похожа на группу."""
    match = _GROUP.match(str(spec or "").strip())
    if not match:
        return None
    prefix, path, first, last = match.groups()
    first = int(first)
    last = first if last is None else int(last)
    if last < first:
        first, last = last, first
    return prefix, path, first, last


def make_group(prefix, path, first, count):
    """Собрать запись группы: ('Gi', '1/0/', 1, 48) -> `Gi1/0/1-48`."""
    prefix = (prefix or "").strip()
    path = (path or "").strip().strip("/")
    path = path + "/" if path else ""
    first = max(0, int(first))
    count = max(1, int(count))
    last = first + count - 1
    if last == first:
        return "%s%s%d" % (prefix, path, first)
    return "%s%s%d-%d" % (prefix, path, first, last)


def group_size(spec):
    parsed = parse_group(spec)
    return parsed[3] - parsed[2] + 1 if parsed else 0


def expand_group(spec):
    """Список имён портов одной группы."""
    parsed = parse_group(spec)
    if not parsed:
        return []
    prefix, path, first, last = parsed
    last = min(last, first + MAX_PORTS - 1)
    return ["%s%s%d" % (prefix, path, n) for n in range(first, last + 1)]


def expand(specs):
    """Все порты устройства по списку групп: по порядку, без повторов."""
    names, seen = [], set()
    for spec in specs or []:
        for name in expand_group(spec):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
            if len(names) >= MAX_PORTS:
                return names
    return names


def describe_group(spec):
    """Подпись группы для списка в интерфейсе."""
    size = group_size(spec)
    return "%s — %d %s" % (spec, size, plural(size)) if size else str(spec)


def plural(count):
    if count % 10 == 1 and count % 100 != 11:
        return "порт"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "порта"
    return "портов"


def looks_like_port(text):
    """Похоже ли на имя порта (Gi1/0/1, ge-0/0/0, Eth1), а не на номер VLAN."""
    return bool(_PORT_NAME.match(str(text or "").strip()))


# ----------------------------------------------------------------- VLAN портов
def parse_vlan(entry):
    """Разобрать запись `vlans` в (порт, режим, [vlan…]).

    Порт пустой — VLAN относится ко всему устройству, как было раньше.
    """
    text = str(entry or "").strip()
    if not text:
        return "", "", []
    match = _ENTRY.match(text)
    if not match or not looks_like_port(match.group(1)):
        return "", "", [text]
    port = match.group(1).strip()
    words = match.group(2).replace(",", " ").split()
    mode = ""
    if words and words[0].lower() in VLAN_MODES:
        mode = words.pop(0).lower()
    return port, mode, words


def format_vlan(port, mode, vlans):
    """Собрать запись обратно: ('Gi1/0/1', 'access', ['10']) -> `Gi1/0/1: access 10`."""
    port = (port or "").strip()
    vlans = [str(v).strip() for v in (vlans or []) if str(v).strip()]
    mode = (mode or "").strip().lower()
    if not port:
        return " ".join(vlans)
    if not vlans and not mode:
        return ""
    return "%s: %s" % (port, " ".join(([mode] if mode else []) + vlans))

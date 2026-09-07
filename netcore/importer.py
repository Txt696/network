"""
Разбор конфигурации Cisco IOS/IOS-XE (`show running-config`) в устройство.

Читает то, что реально есть в конфиге свитча или роутера, и превращает
это в поля Device — имя, тип, IP управления, набор портов, VLAN и режим
каждого порта, и, где получится, аплинки на соседей:

    device, hints = parse_config(text, catalog=vault.devices())

`device` — заготовка Device (ещё не сохранённая), `hints` — список
подсказок для интерфейса (что не удалось разобрать однозначно и решать
пользователю). Порт без описания или с непонятным описанием просто не
попадает в аплинки — это не ошибка, часть портов всегда free-form.

Разбор нарочно терпимый: конфиг реального железа большой и разный,
незнакомые команды внутри блока интерфейса просто пропускаются.
"""

import re

from . import links, ports
from .models import Device, valid_ip

# Полное имя интерфейса, как его печатает IOS -> короткое, как в netcore.ports.
IOS_PORT_NAMES = {
    "GigabitEthernet": "Gi",
    "TenGigabitEthernet": "Te",
    "FastEthernet": "Fa",
    "TwentyFiveGigE": "Twe",
    "FortyGigE": "Fo",
    "HundredGigE": "Hu",
    "Ethernet": "Eth",
    "Port-channel": "Po",
}

# Интерфейсы, которые не физические/агрегированные порты — в набор портов
# их включать незачем (виртуальные VLAN, туннели, loopback). Port-channel
# сюда не входит: агрегат — такой же настраиваемый «порт», как обычный,
# и часто именно на нём (а не на его физических членах) висит описание
# и trunk/access-настройка линка на соседа.
_SKIP_PREFIXES = ("Vlan", "Loopback", "Tunnel", "Null")

_HOSTNAME = re.compile(r"^hostname\s+(\S+)", re.MULTILINE)
_INTERFACE_HEAD = re.compile(r"^interface\s+(\S+)\s*$", re.MULTILINE)
_IP_ADDRESS = re.compile(
    r"^\s*ip address\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)", re.MULTILINE)
_DESCRIPTION = re.compile(r"^\s*description\s+(.+?)\s*$", re.MULTILINE)
_ACCESS_VLAN = re.compile(r"^\s*switchport access vlan\s+(\d+)", re.MULTILINE)
_TRUNK_MODE = re.compile(r"^\s*switchport mode trunk\s*$", re.MULTILINE)
_TRUNK_VLANS = re.compile(r"^\s*switchport trunk allowed vlan\s+(add\s+)?(\S+)", re.MULTILINE)
_SHUTDOWN = re.compile(r"^\s*shutdown\s*$", re.MULTILINE)

# «description To core-sw-01», «To Balkan-Bank», «description to СЛУЖЕБНЫЙ» —
# берём то, что после to/к, до первого разделителя вроде «Cross-1»/скобки/тире.
_UPLINK_HINT = re.compile(
    r"^(?:to|к)\s+([^,()]+?)(?:\s+(?:cross|кросс)[\s-]*\d+.*)?$", re.IGNORECASE)


def _normalize_port_name(raw):
    """`GigabitEthernet0/0/1` -> `Gi0/0/1`; неизвестный тип возвращает как есть."""
    match = re.match(r"^([A-Za-z][A-Za-z-]*)(\d.*)$", raw)
    if not match:
        return raw
    prefix, rest = match.groups()
    return IOS_PORT_NAMES.get(prefix, prefix) + rest


def _iter_blocks(text):
    """Разбить конфиг на блоки `interface X` -> текст блока (со следующими
    строками с отступом, до первой строки без отступа)."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = re.match(r"^interface\s+(\S+)\s*$", lines[i])
        if not match:
            i += 1
            continue
        name = match.group(1)
        body = []
        i += 1
        while i < len(lines) and (lines[i].startswith(" ") or not lines[i].strip()):
            body.append(lines[i])
            i += 1
        yield name, "\n".join(body)


def _detect_kind(text):
    """Router или switch — по тому, что в конфиге реально настроено."""
    router_markers = ("boot system flash:isr", "router eigrp", "router ospf",
                     "router bgp", "crypto isakmp", "crypto map")
    switch_markers = ("switch 1 provision", "switch 2 provision", "spanning-tree mode")
    lowered = text.lower()
    if any(marker in lowered for marker in router_markers):
        return "router"
    if any(marker in lowered for marker in switch_markers):
        return "switch"
    return "other"


def _pick_mgmt_ip(interfaces):
    """Какой из адресов на интерфейсах устройства считать управляющим.

    Предпочитаем Vlan1 (обычная management-VLAN на свитчах), затем
    Loopback, затем первый обычный (не служебный vrf) адрес.
    """
    by_name = {name: info for name, info in interfaces}
    for preferred in ("Vlan1", "Loopback0", "Loopback1"):
        info = by_name.get(preferred)
        if info and info.get("ip"):
            return info["ip"]
    for name, info in interfaces:
        if name.startswith("GigabitEthernet0") and "/" not in name[len("GigabitEthernet0"):]:
            continue  # отдельный OOB-порт (мгмт vrf) — не то, чем обычно заходят
        if info.get("ip") and not info.get("shutdown"):
            return info["ip"]
    return ""


def parse_config(text, catalog=None):
    """Разобрать текст конфигурации.

    `catalog` — список уже известных Device (например, vault.devices()):
    по нему описания портов вида «To <имя>» пробуют сопоставить с
    существующим устройством, чтобы сразу проставить соседа в аплинке.

    Возвращает (device, hints): device — Device (id ещё не задан, вызывающий
    сам решает, новое это устройство или обновление существующего), hints —
    список строк с тем, что стоит проверить руками (нераспознанные подсказки).
    """
    text = text.replace("\r\n", "\n")
    hostname_match = _HOSTNAME.search(text)
    name = hostname_match.group(1) if hostname_match else ""

    interfaces = []       # [(name, {"ip":..., "shutdown":bool}), ...] — для выбора mgmt_ip
    port_names = []       # физические порты в исходном порядке — для группировки
    vlan_entries = []     # готовые строки для Device.vlans
    hints = []
    peer_hints = {}       # port -> сырой текст подсказки (для тех, кого не нашли)

    catalog = list(catalog or [])
    by_name = {d.name.strip().lower(): d for d in catalog}
    by_id = {d.id.lower(): d for d in catalog}

    def find_peer(hint_text):
        key = hint_text.strip().lower()
        exact = by_id.get(key) or by_name.get(key)
        if exact:
            return exact
        # В описаниях порт часто ссылается на сокращённое имя («To BFN-AS-ME»
        # при полном хосте BFN-AS-ME-SB-sw) — ищем по вхождению подстроки в
        # любую сторону, но только если совпадение единственное: неоднозначная
        # подсказка хуже, чем незаполненный аплинк.
        candidates = [d for d in catalog
                     if key in d.name.lower() or d.name.lower() in key
                     or key in d.id.lower() or d.id.lower() in key]
        return candidates[0] if len(candidates) == 1 else None

    for raw_name, body in _iter_blocks(text):
        ip_match = _IP_ADDRESS.search(body)
        shutdown = bool(_SHUTDOWN.search(body))
        interfaces.append((raw_name, {
            "ip": ip_match.group(1) if ip_match else "",
            "shutdown": shutdown,
        }))

        if raw_name.startswith(_SKIP_PREFIXES):
            continue
        short_name = _normalize_port_name(raw_name)
        if not ports.looks_like_port(short_name):
            continue
        port_names.append(short_name)

        access_match = _ACCESS_VLAN.search(body)
        if access_match:
            vlan_entries.append(ports.format_vlan(short_name, "access", [access_match.group(1)]))
        elif _TRUNK_MODE.search(body):
            trunk_vlans = []
            for _add, vlan_list in _TRUNK_VLANS.findall(body):
                trunk_vlans.extend(v for v in vlan_list.replace(",", " ").split() if v)
            vlan_entries.append(ports.format_vlan(short_name, "trunk", trunk_vlans))

        description_match = _DESCRIPTION.search(body)
        if description_match:
            hint_match = _UPLINK_HINT.match(description_match.group(1).strip())
            if hint_match:
                peer_hints[short_name] = hint_match.group(1).strip()

    port_groups = ports.compress(port_names)

    uplinks = []
    for port, hint_text in peer_hints.items():
        peer = find_peer(hint_text)
        if peer is None:
            # Соседа ещё нет в хранилище (например, устройства импортируют
            # по одному) — сохраняем связь как есть, портом соседа. Как только
            # это устройство появится в хранилище под тем же именем и в его
            # заметке будет обратная ссылка сюда, при сохранении она сама
            # доуточнится до полного линка — см. Vault._reflect_link.
            uplinks.append(links.format(port, hint_text, ""))
            hints.append("Порт %s: сосед «%s» не найден в хранилище — привязка "
                         "сохранена по имени, порт соседа впишется сам, когда "
                         "это устройство тоже будет добавлено." % (port, hint_text))
            continue
        # Если у уже известного соседа есть свой порт, направленный сюда без
        # указания порта на этой стороне, — подставляем его: обе стороны
        # получают полный линк уже при импорте, а не только после ручной правки.
        peer_port = _find_reverse_port(peer, name)
        uplinks.append(links.format(port, peer.id, peer_port))

    mgmt_ip = _pick_mgmt_ip(interfaces)
    kind = _detect_kind(text)

    device = Device(
        name=name or "",
        kind=kind if kind != "other" else ("switch" if port_groups else "other"),
        mgmt_ip=mgmt_ip if valid_ip(mgmt_ip) else "",
        vendor="cisco",
        protocol="ssh",
        ports=port_groups,
        uplinks=uplinks,
        vlans=vlan_entries,
        body="## Импортировано из конфигурации\n\n"
             "Устройство «%s» — заполнено автоматически при импорте running-config. "
             "Проверьте порты, VLAN и аплинки перед сохранением.\n" % (name or "?"),
    )
    if not name:
        hints.insert(0, "В конфиге не нашлась команда `hostname` — впишите имя устройства.")
    if not device.mgmt_ip:
        hints.append("Не удалось однозначно определить IP управления — впишите его вручную.")
    return device, hints


def _find_reverse_port(peer, device_name):
    """У соседа уже есть порт, аплинк которого (без указания порта) смотрит
    на устройство с этим именем/id? Вернуть такой порт соседа, иначе "".
    """
    if not device_name:
        return ""
    key = device_name.strip().lower()
    for entry in peer.uplinks:
        local_port, target, target_port = links.parse(entry)
        if local_port and not target_port and target.strip().lower() == key:
            return local_port
    return ""

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
from pathlib import Path

from . import links, ports
from .models import Device, slugify, valid_ip

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
_CHANNEL_GROUP = re.compile(r"^\s*channel-group\s+(\d+)", re.MULTILINE)

# «description To core-sw-01», «For SB-AS-Master-rt», «To BFN-AS-ME (Port-channel1)»,
# «To SB-AS-Master-rt Cross-1», «To TMCell (Vl40-SMS-Gate, Vl135-APN)» — берём имя
# соседа после to/for/к/для и отбрасываем хвост: скобку с пояснением, перечисление
# через запятую, номер кросса.
_UPLINK_HINT = re.compile(
    r"^(?:to|for|к|для)\s+([^,(]+?)"
    r"(?:\s*[,(].*)?"
    r"(?:\s+(?:cross|кросс)[\s-]*\d*.*)?$", re.IGNORECASE)


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
    channel_of = {}       # порт -> агрегат, в который он входит (channel-group)

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
        if len(candidates) == 1:
            return candidates[0]
        # Похожих несколько («To BFN-AS-ME» при BFN-AS-ME-SB-sw и
        # BFN-AS-ME-SB-tkm-sw). Тогда спрашиваем встречную сторону: сосед
        # тот, у кого в описаниях портов упомянуты мы. Если и так неясно —
        # оставляем связь по имени, гадать не надо.
        answering = [d for d in candidates if _refers_to(d, name)]
        return answering[0] if len(answering) == 1 else None

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

        channel_match = _CHANNEL_GROUP.search(body)
        if channel_match:
            channel_of[short_name] = "Po" + channel_match.group(1)

        description_match = _DESCRIPTION.search(body)
        if description_match:
            hint_match = _UPLINK_HINT.match(description_match.group(1).strip())
            if hint_match:
                peer_hints[short_name] = hint_match.group(1).strip()

    port_groups = ports.compress(port_names)

    # Порт, входящий в агрегат, не заводит вторую связь к тому же соседу:
    # физически кабелей столько, сколько членов, но линк один и живёт на
    # Port-channel. Описание члена («To SB-AS-Master-rt Cross-1») остаётся
    # в конфиге, а на карте линия не задваивается.
    bundled = sorted(port for port, aggregate in channel_of.items()
                     if port in peer_hints and aggregate in peer_hints)
    for port in bundled:
        peer_hints.pop(port)
    if bundled:
        hints.append("Порты %s входят в агрегат — связь записана на %s, "
                     "а не на каждом порту отдельно."
                     % (", ".join(bundled),
                        ", ".join(sorted({channel_of[p] for p in bundled}))))

    uplinks = []
    taken = set()          # порты соседей, уже занятые линком из этого конфига
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
        # Один порт соседа — один линк. Два наших порта с одинаковым
        # описанием (обычно члены одного агрегата) не могут оба сидеть в
        # его Po1: второму оставляем связь без порта, чем врать про порт.
        if peer_port and (peer.id.lower(), peer_port.lower()) in taken:
            peer_port = ""
        if peer_port:
            taken.add((peer.id.lower(), peer_port.lower()))
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


def _refers_to(device, name):
    """Есть ли у устройства аплинк, указывающий на устройство с таким именем?

    Сравнение такое же терпимое, как при поиске соседа: описания портов
    называют друг друга то полным именем, то сокращённым.
    """
    key = (name or "").strip().lower()
    if not key:
        return False
    for entry in device.uplinks:
        target = links.parse(entry)[1].strip().lower()
        if target and (target == key or target in key or key in target):
            return True
    return False


def _find_reverse_port(peer, device_name, device_id=""):
    """У соседа уже есть порт, аплинк которого (без указания порта) смотрит
    на это устройство? Вернуть такой порт соседа, иначе "".

    Точное совпадение имени/id — лучший случай. Но в описаниях порт часто
    называет соседа сокращённо («To BFN-AS-ME» при хосте BFN-AS-ME-SB-sw),
    поэтому при отсутствии точного берём и совпадение по вхождению — и
    только если оно единственное: гадать, какой из похожих портов наш,
    хуже, чем оставить порт соседа незаполненным.
    """
    keys = {key.strip().lower() for key in (device_name, device_id) if (key or "").strip()}
    if not keys:
        return ""
    exact, fuzzy = [], []
    for entry in peer.uplinks:
        local_port, target, target_port = links.parse(entry)
        if not local_port or target_port:
            continue
        key = target.strip().lower()
        if key in keys:
            exact.append(local_port)
        elif any(key in known or known in key for known in keys):
            fuzzy.append(local_port)
    if exact:
        return exact[0]
    return fuzzy[0] if len(fuzzy) == 1 else ""


# --------------------------------------------------------- пакетный импорт

# Чем обычно называют выгруженный конфиг. Расширение — только фильтр для
# папки: файл, выбранный руками, читается с любым именем.
CONFIG_SUFFIXES = (".txt", ".cfg", ".conf", ".log", ".ios", ".run", ".rsc")


def collect_files(paths, recursive=True):
    """Пути (файлы и/или папки) -> отсортированный список файлов конфигов.

    Папка разворачивается в лежащие в ней файлы с подходящим расширением
    (по умолчанию вместе с подпапками); файл берётся как есть, каким бы
    ни было его имя. Дубли путей отбрасываются.
    """
    found, seen = [], set()
    for raw in paths or []:
        path = Path(raw)
        if path.is_dir():
            pattern = "**/*" if recursive else "*"
            items = sorted(item for item in path.glob(pattern)
                           if item.is_file() and item.suffix.lower() in CONFIG_SUFFIXES)
        elif path.is_file():
            items = [path]
        else:
            continue
        for item in items:
            key = str(item.resolve())
            if key not in seen:
                seen.add(key)
                found.append(item)
    return found


def _blend_uplinks(existing, parsed):
    """Аплинки конфига поверх уже записанных, по своему порту.

    Конфиг знает только о портах, у которых есть описание соседа: остальные
    он не «отменяет», он про них просто молчит. Поэтому запись из конфига
    вытесняет прежнюю запись того же порта, а аплинки портов, которых в
    конфиге не было (проставленные руками или пришедшие обратной
    синхронизацией от соседа), остаются на месте.
    """
    taken = {links.parse(entry)[0].lower() for entry in parsed
             if links.parse(entry)[0]}
    kept = [entry for entry in existing or []
            if links.parse(entry)[0].lower() not in taken]
    return list(parsed) + kept


def merge_into(existing, parsed):
    """Наложить разобранный конфиг на уже существующую карточку.

    Сеть (порты/VLAN/IP/вендор/аплинки) берём из конфига — он свежее.
    Всё остальное (теги, площадку, стойку, ссылку на доступы, статус)
    трогать незачем: это ручные данные, конфиг о них ничего не знает.
    """
    fields = existing.to_meta()
    fields.update({
        "name": parsed.name or existing.name,
        "kind": parsed.kind,
        "mgmt_ip": parsed.mgmt_ip or existing.mgmt_ip,
        "vendor": parsed.vendor or existing.vendor,
        "protocol": parsed.protocol or existing.protocol,
        "ports": parsed.ports,
        "uplinks": _blend_uplinks(existing.uplinks, parsed.uplinks),
        "vlans": parsed.vlans,
        "created": existing.created,
    })
    body = existing.body
    if parsed.body.strip() and parsed.body.strip() not in body:
        body = (body.rstrip() + "\n\n" + parsed.body).strip()
    return Device(device_id=existing.id, body=body, **fields)


def import_files(vault, paths, recursive=True, site=""):
    """Разобрать и сохранить в хранилище сразу пачку конфигов.

    Читает все файлы, разбирает их дважды: первый раз — чтобы узнать, какие
    устройства вообще есть в этой пачке, второй — уже зная их, чтобы
    описания вида «description To core-sw-01» находили соседа даже тогда,
    когда его собственный конфиг лежит в той же папке. Дальше каждое
    устройство сохраняется: одноимённое обновляется, новое заводится.
    `site` проставляется только новым записям — у существующих площадка
    заполнена руками, и конфиг о ней ничего не знает.

    Возвращает список записей по файлу:
    {"path", "name", "device_id", "action", "ports", "vlans", "uplinks",
     "hints", "error"}, где action — created / updated / failed.
    """
    report = []
    drafts = []                     # [(path, черновик, существующий|None, запись)]
    stored = vault.devices()
    by_key = {d.name.strip().lower(): d for d in stored}

    for path in collect_files(paths, recursive):
        record = {"path": str(path), "name": "", "device_id": "", "action": "failed",
                  "ports": 0, "vlans": 0, "uplinks": 0, "hints": [], "error": ""}
        report.append(record)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            record["error"] = "не удалось прочитать файл: %s" % exc
            continue
        if not _HOSTNAME.search(text.replace("\r\n", "\n")):
            # Имя устройства пакетному импорту взять неоткуда (в отличие от
            # импорта одного файла, где его можно вписать руками в форму).
            record["error"] = ("в файле нет команды hostname — не похоже на "
                               "конфигурацию Cisco")
            continue
        device, _hints = parse_config(text, catalog=stored)
        key = device.name.strip().lower()
        existing = by_key.get(key) or next(
            (d for d in stored if d.id == slugify(device.name)), None)
        # id решаем сразу: во втором проходе соседи ссылаются друг на друга
        # именно по нему, и он должен совпасть с тем, под которым сохранимся.
        device.id = existing.id if existing is not None else vault.unique_id(device.name)
        record["name"] = device.name
        drafts.append((path, device, existing, record))

    # Второй проход: теперь в каталоге есть и соседи из этой же пачки.
    batch = [draft for _p, draft, _e, _r in drafts]
    for path, first_pass, existing, record in drafts:
        catalog = [d for d in stored if d.id != first_pass.id]
        catalog += [d for d in batch if d.id != first_pass.id]
        device, hints = parse_config(path.read_text(encoding="utf-8", errors="replace"),
                                     catalog=catalog)
        device.id = first_pass.id
        if existing is not None:
            device = merge_into(existing, device)
            record["action"] = "updated"
        else:
            device.site = site
            record["action"] = "created"
        try:
            vault.save(device)
        except (OSError, ValueError) as exc:
            record["action"] = "failed"
            record["error"] = "не удалось сохранить: %s" % exc
            continue
        record.update({"device_id": device.id, "hints": hints,
                       "ports": len(device.ports), "vlans": len(device.vlans),
                       "uplinks": len(device.uplinks)})
    return report


def format_report(report):
    """Отчёт пакетного импорта человеческим текстом."""
    ok = [r for r in report if r["action"] != "failed"]
    bad = [r for r in report if r["action"] == "failed"]
    lines = ["Файлов обработано: %d, устройств записано: %d." % (len(report), len(ok))]
    for record in ok:
        lines.append("%s — %s: %s, портов %d, VLAN %d, аплинков %d"
                     % (Path(record["path"]).name, record["device_id"],
                        "обновлено" if record["action"] == "updated" else "добавлено",
                        record["ports"], record["vlans"], record["uplinks"]))
        for hint in record["hints"]:
            lines.append("    · " + hint)
    if bad:
        lines.append("")
        lines.append("Пропущено:")
        for record in bad:
            lines.append("%s — %s" % (Path(record["path"]).name, record["error"]))
    return "\n".join(lines)

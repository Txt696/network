"""Модель устройства: Markdown-заметка с YAML-фронтматтером."""

import ipaddress
import re
import unicodedata
from datetime import datetime, timezone

from . import frontmatter, links, ports

KINDS = ("server", "switch", "router", "firewall", "ap", "storage", "pdu", "other")
PROTOCOLS = ("ssh", "telnet", "serial", "https", "rdp", "vnc", "none")
STATUSES = ("active", "spare", "maintenance", "decommissioned", "planned")

DEFAULT_PORTS = {"ssh": 22, "telnet": 23, "https": 443, "rdp": 3389, "vnc": 5900}

# Порядок полей во фронтматтере — файлы должны выглядеть одинаково.
FIELD_ORDER = (
    "name", "kind", "mgmt_ip", "hostname", "vendor", "model", "os_version",
    "serial", "site", "room", "rack", "unit", "role", "protocol", "port",
    "secret", "status", "owner", "tags", "ports", "uplinks", "vlans", "created", "updated",
)

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}


def now_stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(text):
    """Имя файла из названия устройства (латиница, кириллица транслитом)."""
    text = (text or "").strip().lower()
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "device"


def valid_ip(value):
    try:
        ipaddress.ip_address(str(value).strip())
        return True
    except ValueError:
        return False


def _as_list(value):
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).replace(";", ",").split(",") if part.strip()]


class Device:
    """Одно устройство: серверы, свитчи, роутеры и всё остальное железо."""

    def __init__(self, device_id=None, **fields):
        self.id = device_id or slugify(fields.get("name", ""))
        self.name = fields.get("name") or self.id
        self.kind = fields.get("kind") or "other"
        self.mgmt_ip = fields.get("mgmt_ip", "")
        self.hostname = fields.get("hostname", "")
        self.vendor = fields.get("vendor", "")
        self.model = fields.get("model", "")
        self.os_version = fields.get("os_version", "")
        self.serial = fields.get("serial", "")
        self.site = fields.get("site", "")
        self.room = fields.get("room", "")
        self.rack = fields.get("rack", "")
        self.unit = fields.get("unit", "")
        self.role = fields.get("role", "")
        self.protocol = (fields.get("protocol") or "ssh").lower()
        self.port = int(fields.get("port") or DEFAULT_PORTS.get(self.protocol, 22))
        self.secret = fields.get("secret", "")
        self.status = fields.get("status") or "active"
        self.owner = fields.get("owner", "")
        self.tags = _as_list(fields.get("tags"))
        self.ports = _as_list(fields.get("ports"))
        self.uplinks = _as_list(fields.get("uplinks"))
        self.vlans = _as_list(fields.get("vlans"))
        self.created = fields.get("created") or now_stamp()
        self.updated = fields.get("updated") or self.created
        self.body = fields.get("body", "")
        # Всё, что пользователь добавил во фронтматтер руками, сохраняем как есть.
        self.extra = {
            k: v for k, v in fields.items()
            if k not in FIELD_ORDER and k not in ("body", "id")
        }

    # ------------------------------------------------------------ свойства
    @property
    def target(self):
        """Адрес для подключения: mgmt_ip, иначе hostname."""
        return self.mgmt_ip or self.hostname

    @property
    def secret_ref(self):
        """Ссылка на запись доступов (по умолчанию — id устройства)."""
        return self.secret or self.id

    def label(self):
        return "%s (%s)" % (self.name, self.target) if self.target else self.name

    def port_names(self):
        """Имена всех портов: `ports: [Gi1/0/1-48]` -> Gi1/0/1 … Gi1/0/48."""
        return ports.expand(self.ports)

    def configured_ports(self):
        """Порты, у которых что-то настроено — аплинк или VLAN."""
        names = [links.parse(entry)[0] for entry in self.uplinks]
        names += [ports.parse_vlan(entry)[0] for entry in self.vlans]
        seen, result = set(), []
        for name in names:
            if name and name.lower() not in seen:
                seen.add(name.lower())
                result.append(name)
        return result

    # ---------------------------------------------------------- валидация
    def validate(self):
        """Список проблем; пустой — значит всё в порядке."""
        problems = []
        if not self.name.strip():
            problems.append("не задано имя устройства")
        if self.kind not in KINDS:
            problems.append("неизвестный тип: %s (допустимо: %s)" % (self.kind, ", ".join(KINDS)))
        if self.mgmt_ip and not valid_ip(self.mgmt_ip):
            problems.append("некорректный IP: %s" % self.mgmt_ip)
        if self.protocol not in PROTOCOLS:
            problems.append("неизвестный протокол: %s" % self.protocol)
        if not 1 <= self.port <= 65535:
            problems.append("порт вне диапазона: %s" % self.port)
        if self.status not in STATUSES:
            problems.append("неизвестный статус: %s" % self.status)
        if not self.target:
            problems.append("нет ни IP, ни hostname — подключиться будет нельзя")
        if self.ports:
            known = {name.lower() for name in self.port_names()}
            unknown = [p for p in self.configured_ports() if p.lower() not in known]
            if unknown:
                problems.append("настроены порты, которых нет в списке портов: %s"
                                % ", ".join(unknown))
        return problems

    # -------------------------------------------------------- сериализация
    def to_meta(self):
        meta = {
            "name": self.name, "kind": self.kind, "mgmt_ip": self.mgmt_ip,
            "hostname": self.hostname, "vendor": self.vendor, "model": self.model,
            "os_version": self.os_version, "serial": self.serial, "site": self.site,
            "room": self.room, "rack": self.rack, "unit": self.unit, "role": self.role,
            "protocol": self.protocol, "port": self.port, "secret": self.secret,
            "status": self.status, "owner": self.owner, "tags": self.tags,
            "ports": self.ports, "uplinks": self.uplinks, "vlans": self.vlans,
            "created": self.created, "updated": self.updated,
        }
        meta.update(self.extra)
        return meta

    def to_markdown(self):
        return frontmatter.dump(self.to_meta(), self.body, key_order=FIELD_ORDER)

    @classmethod
    def from_markdown(cls, text, device_id=None):
        meta, body = frontmatter.parse(text)
        return cls(device_id=device_id, body=body, **meta)

    def searchable_text(self):
        values = [str(v) for v in self.to_meta().values() if not isinstance(v, (list, dict))]
        values.extend(self.tags + self.ports + self.uplinks + self.vlans)
        values.append(self.body)
        values.append(self.id)
        return "\n".join(values).lower()

    def __repr__(self):
        return "<Device %s %s>" % (self.id, self.target)

"""
Сервер карты сети: отдаёт страницу и данные из хранилища.

Только просмотр. Хранилище не разблокируется, файл secrets.enc не
читается вовсе — паролей в браузере нет и быть не может.
Слушает только 127.0.0.1: снаружи в него не попасть.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from netcore import Vault

STATIC = Path(__file__).resolve().parent / "static"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Поля устройства, которые уходят в браузер. Паролей тут нет и не будет:
# они лежат в secrets.enc, который карта вообще не открывает.
CARD_FIELDS = ("id", "name", "kind", "mgmt_ip", "hostname", "vendor", "model",
               "os_version", "serial", "site", "room", "rack", "unit", "role",
               "protocol", "port", "status", "owner", "tags", "vlans", "updated")


def device_card(device):
    return {field: getattr(device, field, "") for field in CARD_FIELDS}


def build_map(vault):
    """Данные карты: регионы с устройствами и связи между ними."""
    devices = vault.devices()
    sites = {}
    for device in devices:
        site = (device.site or "").strip() or "Без региона"
        sites.setdefault(site, []).append({
            "id": device.id, "name": device.name, "kind": device.kind,
            "mgmt_ip": device.mgmt_ip, "vendor": device.vendor,
            "status": device.status,
        })
    ordered = [{"name": name, "devices": sorted(items, key=lambda d: d["name"].lower())}
               for name, items in sorted(sites.items())]
    return {"sites": ordered, "links": vault.links(devices),
            "vault": str(vault.path), "total": len(devices)}


class MapHandler(BaseHTTPRequestHandler):
    """Обработчик запросов. Хранилище кладётся в vault атрибутом класса."""

    vault = None
    server_version = "NetMap"

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/":
            return self._file("map.html", "text/html; charset=utf-8")
        if path == "/favicon.ico":
            return self._send(204, "image/x-icon", b"")  # иконки нет, и ладно
        if path == "/api/map":
            return self._json(build_map(self.vault))
        if path.startswith("/api/device/"):
            return self._device(path[len("/api/device/"):])
        self._error(404, "Нет такой страницы")

    def _device(self, device_id):
        device = self.vault.get(device_id)
        if device is None:
            return self._error(404, "Устройство не найдено: %s" % device_id)
        card = device_card(device)
        card["body"] = device.body
        card["links"] = [link for link in self.vault.links()
                         if device.id in (link["source"], link["target"])]
        card["notes"] = [path.name for path in self.vault.device_notes(device.id)]
        return self._json(card)

    # ------------------------------------------------------------- ответы
    def _json(self, payload):
        self._send(200, "application/json; charset=utf-8",
                   json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _file(self, name, content_type):
        path = STATIC / name
        if not path.exists():
            return self._error(404, "Файл не найден: %s" % name)
        self._send(200, content_type, path.read_bytes())

    def _error(self, code, message):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps({"error": message}, ensure_ascii=False).encode("utf-8"))

    def _send(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        """Молчим: карта не должна засорять консоль."""


def make_server(vault, port=DEFAULT_PORT):
    """Поднять сервер на локальном адресе. Хранилище открывается закрытым."""
    vault = vault if isinstance(vault, Vault) else Vault(vault)
    vault.require_vault()
    handler = type("BoundMapHandler", (MapHandler,), {"vault": vault})
    return ThreadingHTTPServer((HOST, port), handler)

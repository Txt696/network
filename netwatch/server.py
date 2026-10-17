"""
Веб-страница мониторинга: таблица компьютеров, счётчики и журнал событий.

Только чтение данных, которые и так видит любой пользователь домена
(имена, ОС, подразделения). Паролей и учётных данных здесь нет.
По умолчанию слушает 127.0.0.1; открыть в сеть — ключом --host 0.0.0.0.
"""

import csv
import io
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770

CSV_COLUMNS = (("Компьютер", "name"), ("Статус", "status"), ("IP", "address"),
               ("ОС", "os"), ("Подразделение", "ou"), ("Последний ответ", "last_seen"),
               ("Вход в домен", "last_logon"), ("В статусе с", "since"),
               ("Описание", "description"))
TIME_FIELDS = ("last_seen", "last_logon", "since")


def export_csv(snapshot):
    """Таблица для Excel: точка с запятой и BOM, иначе русский Excel её не поймёт."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow([title for title, _ in CSV_COLUMNS])
    for record in snapshot["computers"]:
        row = []
        for _, field in CSV_COLUMNS:
            value = record.get(field)
            if field == "status":
                value = snapshot["labels"].get(value, value)
            elif field in TIME_FIELDS:
                value = time.strftime("%Y-%m-%d %H:%M", time.localtime(value)) if value else ""
            row.append(value or "")
        writer.writerow(row)
    return ("﻿" + out.getvalue()).encode("utf-8")


class WatchHandler(BaseHTTPRequestHandler):
    """Обработчик запросов. Монитор кладётся в monitor атрибутом класса."""

    monitor = None
    server_version = "NetWatch"

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/":
            return self._file("watch.html", "text/html; charset=utf-8")
        if path == "/favicon.ico":
            return self._send(204, "image/x-icon", b"")
        if path == "/api/status":
            return self._json(self.monitor.snapshot())
        if path == "/api/export.csv":
            body = export_csv(self.monitor.snapshot())
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="netwatch.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None
        self._error(404, "Нет такой страницы")

    def do_POST(self):
        if self.path.rstrip("/") == "/api/check":
            self.monitor.check_now()
            return self._send(202, "application/json; charset=utf-8", b'{"ok": true}')
        self._error(404, "Нет такой страницы")

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
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        """Молчим: каждый запрос страницы в консоли не нужен."""


def make_server(monitor, host=DEFAULT_HOST, port=DEFAULT_PORT):
    handler = type("BoundWatchHandler", (WatchHandler,), {"monitor": monitor})
    return ThreadingHTTPServer((host, port), handler)

"""
Веб-страница мониторинга: вкладка компьютеров (кто на связи) и вкладка
журналов событий Windows со справочником, что какое событие значит.

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

from netwatch import catalog

STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770
MAX_EXPORT = 100000  # в CSV — все хранимые события, а не только показанные
NO_EVENTS = "Сбор журналов выключен (--event-hosts none)."

CSV_COLUMNS = (("Компьютер", "name"), ("Статус", "status"), ("IP", "address"),
               ("ОС", "os"), ("Подразделение", "ou"), ("Последний ответ", "last_seen"),
               ("Вход в домен", "last_logon"), ("В статусе с", "since"),
               ("Описание", "description"))
TIME_FIELDS = ("last_seen", "last_logon", "since")


def local_time(stamp):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp)) if stamp else ""


def csv_bytes(header, rows):
    """Таблица для Excel: точка с запятой и BOM, иначе русский Excel её не поймёт."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(header)
    writer.writerows(rows)
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def export_csv(snapshot):
    rows = []
    for record in snapshot["computers"]:
        row = []
        for _, field in CSV_COLUMNS:
            value = record.get(field)
            if field == "status":
                value = snapshot["labels"].get(value, value)
            elif field in TIME_FIELDS:
                value = local_time(value)
            row.append(value or "")
        rows.append(row)
    return csv_bytes([title for title, _ in CSV_COLUMNS], rows)


def export_events_csv(snapshot):
    rows = []
    for event in snapshot["events"]:
        summary = event["summary"]
        if isinstance(summary, list):
            summary = " · ".join("%s: %s" % (label, value) for label, value in summary)
        rows.append([local_time(event["time"]), catalog.LEVELS.get(event["severity"], ""),
                     event["host"], event["log"], event["id"], event["title"], summary])
    return csv_bytes(["Время", "Важность", "Компьютер", "Журнал", "Код", "Событие",
                      "Подробности"], rows)


class WatchHandler(BaseHTTPRequestHandler):
    """Обработчик запросов. Монитор и сборщик журналов — атрибуты класса."""

    monitor = None
    collector = None
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
            return self._csv("netwatch.csv", export_csv(self.monitor.snapshot()))
        if path == "/api/eventlog":
            return self._json(self.collector.snapshot() if self.collector
                              else {"enabled": False, "error": NO_EVENTS})
        if path == "/api/eventlog/catalog":
            return self._json({"events": catalog.as_reference(), "levels": catalog.LEVELS})
        if path == "/api/eventlog.csv" and self.collector:
            return self._csv("netwatch-events.csv",
                             export_events_csv(self.collector.snapshot(limit=MAX_EXPORT)))
        self._error(404, "Нет такой страницы")

    def do_POST(self):
        path = self.path.rstrip("/")
        if path == "/api/check":
            self.monitor.check_now()
            return self._send(202, "application/json; charset=utf-8", b'{"ok": true}')
        if path == "/api/eventlog/collect" and self.collector:
            self.collector.collect_now()
            return self._send(202, "application/json; charset=utf-8", b'{"ok": true}')
        self._error(404, "Нет такой страницы")

    # ------------------------------------------------------------- ответы
    def _json(self, payload):
        self._send(200, "application/json; charset=utf-8",
                   json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _csv(self, filename, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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


def make_server(monitor, host=DEFAULT_HOST, port=DEFAULT_PORT, collector=None):
    handler = type("BoundWatchHandler", (WatchHandler,),
                   {"monitor": monitor, "collector": collector})
    return ThreadingHTTPServer((host, port), handler)

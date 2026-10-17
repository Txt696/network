"""
Цикл мониторинга: перечитать список компьютеров, проверить каждый,
понять, кто на связи, а кто пропал, и записать, что изменилось.

Состояние хранится в state.json: когда ПК последний раз отвечал и с какого
момента он в текущем статусе — переживает перезапуск программы.
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from netwatch import probe as probe_module
from netwatch.directory import DirectoryError

ONLINE, DOMAIN, OFFLINE, LOST, DISABLED = "online", "domain", "offline", "lost", "disabled"
LABELS = {
    ONLINE: "На связи",
    DOMAIN: "Нет связи с доменом",
    OFFLINE: "Не отвечает",
    LOST: "Пропал",
    DISABLED: "Отключён в AD",
}
DAY = 86400
FAIL_LIMIT = 2     # столько проверок подряд без ответа, прежде чем считать ПК выключенным
MAX_EVENTS = 1000
WORKERS = 64


def classify(record, now, lost_after, stale_after):
    """
    Статус компьютера.

    «Нет связи с доменом» — ПК в сети отвечает, но давно не отмечался в домене:
    lastLogonTimestamp или смена пароля учётной записи компьютера старше
    stale_after. Обычно это сломанное доверие («Не удалось установить
    доверительные отношения…») — машина работает, а войти доменной учёткой нельзя.

    «Пропал» — не отвечает дольше lost_after. Пока своих наблюдений нет
    (первый запуск), берётся время последнего входа в домен из AD.
    """
    if not record.get("enabled", True):
        return DISABLED
    if record.get("online"):
        stamps = [s for s in (record.get("last_logon"), record.get("pwd_last_set")) if s]
        return DOMAIN if any(now - s > stale_after for s in stamps) else ONLINE
    seen = [s for s in (record.get("last_seen"), record.get("last_logon")) if s]
    if not seen or now - max(seen) > lost_after:
        return LOST
    return OFFLINE


class Monitor:
    def __init__(self, load_computers, state_path, interval=120, lost_days=3, stale_days=45,
                 probe=probe_module.check, workers=WORKERS, clock=time.time, source=""):
        self.load_computers = load_computers
        self.state_path = Path(state_path)
        self.interval = interval
        self.lost_after = lost_days * DAY
        self.stale_after = stale_days * DAY
        self.probe = probe
        self.workers = workers
        self.clock = clock
        self.source = source
        self.records = {}
        self.events = []
        self.cycle = {}
        self.busy = False
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._load_state()

    # ------------------------------------------------------------ цикл
    def run_forever(self):
        while not self._stop.is_set():
            self.run_once()
            self._wake.wait(self.interval)
            self._wake.clear()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def check_now(self):
        """Не ждать интервала — проверить сразу (кнопка на странице)."""
        self._wake.set()

    def run_once(self):
        started = self.clock()
        self.busy = True
        error = ""
        try:
            try:
                computers = self.load_computers()
            except DirectoryError as exc:
                computers, error = None, str(exc)
            with self._lock:
                if computers is not None:
                    self._merge(computers, started)
                targets = [(key, rec["host"]) for key, rec in self.records.items()
                           if rec["enabled"]]
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                results = list(pool.map(lambda item: (item[0], self.probe(item[1])), targets))
            with self._lock:
                now = self.clock()
                for key, result in results:
                    if key in self.records:
                        self._apply(self.records[key], result, now)
                self._reclassify(now)
                self.cycle = {"started": started, "finished": now,
                              "duration": round(now - started, 1),
                              "checked": len(results), "error": error}
                self._save_state()
        except Exception as exc:  # цикл не должен умирать из-за одной ошибки
            self.cycle = {"started": started, "finished": self.clock(),
                          "error": "%s: %s" % (type(exc).__name__, exc)}
        finally:
            self.busy = False

    # ------------------------------------------------------- обработка
    def _merge(self, computers, now):
        """Свести свежий список из AD с тем, что уже знаем."""
        first_load = not self.records
        fresh = {}
        for computer in computers:
            key = computer.name.lower()
            record = self.records.get(key) or {
                "name": computer.name, "online": None, "fails": 0, "address": "",
                "how": "", "last_seen": None, "last_check": None,
                "status": "", "since": now}
            if key not in self.records and not first_load:
                self._event(now, computer.name, "", "", "появился в списке")
            record.update(name=computer.name, host=computer.host, os=computer.os,
                          ou=computer.ou, description=computer.description,
                          enabled=computer.enabled, last_logon=computer.last_logon,
                          pwd_last_set=computer.pwd_last_set)
            fresh[key] = record
        for key, record in self.records.items():
            if key not in fresh:
                self._event(now, record["name"], record.get("status", ""), "",
                            "удалён из списка")
        self.records = fresh

    def _apply(self, record, result, now):
        record["last_check"] = now
        record["how"] = result.how
        if result.address:
            record["address"] = result.address
        if result.alive:
            record.update(online=True, fails=0, last_seen=now)
        else:
            record["fails"] = record.get("fails", 0) + 1
            # Одну потерянную проверку прощаем: сеть могла икнуть.
            if record["online"] is None or record["fails"] >= FAIL_LIMIT:
                record["online"] = False

    def _reclassify(self, now):
        for record in self.records.values():
            status = classify(record, now, self.lost_after, self.stale_after)
            if status != record.get("status"):
                if record.get("status"):
                    self._event(now, record["name"], record["status"], status, record["how"])
                record["status"] = status
                record["since"] = now

    def _event(self, now, name, old, new, note):
        self.events.append({"time": now, "name": name, "from": old, "to": new, "note": note})
        del self.events[:-MAX_EVENTS]

    # ------------------------------------------------------- состояние
    def snapshot(self):
        """Всё, что нужно странице: компьютеры, счётчики, события."""
        with self._lock:
            computers = sorted((dict(rec) for rec in self.records.values()),
                               key=lambda rec: rec["name"].lower())
            events = list(reversed(self.events[-300:]))
            cycle = dict(self.cycle)
        counts = {status: 0 for status in LABELS}
        for record in computers:
            if record["status"] in counts:
                counts[record["status"]] += 1
        return {"computers": computers, "events": events, "counts": counts,
                "labels": LABELS, "cycle": cycle, "busy": self.busy, "now": self.clock(),
                "source": self.source, "interval": self.interval,
                "lost_days": self.lost_after / DAY, "stale_days": self.stale_after / DAY}

    def _load_state(self):
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.records = {key: rec for key, rec in state.get("records", {}).items()
                        if isinstance(rec, dict) and rec.get("name")}
        self.events = list(state.get("events", []))[-MAX_EVENTS:]

    def _save_state(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps({"records": self.records, "events": self.events},
                                   ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.state_path)

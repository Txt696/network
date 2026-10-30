"""
Сбор журналов событий Windows с контроллеров домена и других компьютеров.

Журналы читаются через Get-WinEvent: с этой машины — напрямую, с других —
по сети (-ComputerName). Берутся только события из справочника (catalog.py),
и только новые: для каждого компьютера и журнала запоминается время
последнего события, повторы отсекаются по номеру записи.

Чтобы читать чужие журналы по сети, учётной записи, от которой запущен
NetWatch, нужно быть в группе «Читатели журнала событий» (Event Log Readers)
на том компьютере, а в его брандмауэре — разрешено «Удалённое управление
журналом событий». Подробности — в README.
"""

import json
import os
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from netwatch import catalog, powershell

FETCH_TIMEOUT = 180
MAX_PER_LOG = 500     # столько событий одного журнала за раз, не больше
MAX_LOGONS = 5000     # входов за раз: их много, а нужны только последние
RECENT_USERS = 5      # сколько последних пользователей помнить на компьютер
MAX_STORED = 5000
MAX_MESSAGE = 2000
WORKERS = 8           # одновременно опрашиваемых компьютеров

FETCH_SCRIPT = r"""
  $computer = __HOST__
  $queries = @(__QUERIES__)
  $rows = New-Object System.Collections.ArrayList
  $errors = @{}
  foreach ($q in $queries) {
    $params = @{ FilterXml = [xml]$q.xml; MaxEvents = $q.max; ErrorAction = 'Stop' }
    if ($computer) { $params.ComputerName = $computer }
    try { $events = @(Get-WinEvent @params) }
    catch {
      if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $events = @() }
      else { $errors[$q.log] = $_.Exception.Message; continue }
    }
    foreach ($e in $events) {
      $data = @{}
      $index = 0
      foreach ($section in ([xml]$e.ToXml()).DocumentElement.ChildNodes) {
        if ($section.LocalName -eq 'EventData') { $items = $section.ChildNodes }
        elseif ($section.LocalName -eq 'UserData' -and $section.FirstChild) { $items = $section.FirstChild.ChildNodes }
        else { continue }
        foreach ($item in $items) {
          if ($item.NodeType -ne 'Element') { continue }
          $index++
          $name = $item.GetAttribute('Name')
          if (-not $name) { $name = if ($item.LocalName -ne 'Data') { $item.LocalName } else { [string]$index } }
          $data[$name] = $item.InnerText
        }
      }
      $message = ''
      if ($q.message) { try { $message = [string]$e.Message } catch {} }
      if ($message.Length -gt __MAX_MESSAGE__) { $message = $message.Substring(0, __MAX_MESSAGE__) }
      [void]$rows.Add(@{
        query = $q.log; log = $e.LogName; provider = $e.ProviderName; id = $e.Id; record = $e.RecordId
        level = [int]$e.Level; data = $data; message = $message
        time = ([DateTimeOffset]$e.TimeCreated).ToUnixTimeMilliseconds() / 1000.0
      })
    }
  }
  Say 'OK:' (ConvertTo-Json -InputObject @{ events = @($rows); errors = $errors } -Compress -Depth 4)
"""


class EventLogError(Exception):
    """Журналы с компьютера не прочитать."""


def iso_utc(stamp):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(stamp))


def fetch(host, since, local=False, run=subprocess.run):
    """
    События из справочника с одного компьютера.
    since — {запрос: unix-время}, с какого момента брать; запросы — журналы
    System и Security и отдельный запрос входов пользователей (catalog.LOGONS).
    Возвращает (события, {запрос: ошибка}).
    """
    queries = ", ".join(
        "@{ log = %s; xml = %s; max = %d; message = $%s }" % (
            powershell.quote(name), powershell.quote(catalog.query_for(name, iso_utc(since[name]))),
            MAX_LOGONS if name == catalog.LOGONS else MAX_PER_LOG,
            "false" if name == catalog.LOGONS else "true")
        for name in catalog.QUERIES)
    body = (FETCH_SCRIPT.replace("__HOST__", powershell.quote("" if local else host))
            .replace("__QUERIES__", queries).replace("__MAX_MESSAGE__", str(MAX_MESSAGE)))
    try:
        answer = powershell.run(body, FETCH_TIMEOUT, run=run)
    except powershell.NoPowerShell:
        raise EventLogError("Журналы событий читаются только на Windows: PowerShell не найден")
    except powershell.PowerShellError as exc:
        raise EventLogError(str(exc))
    events = answer.get("events") or []
    events = [events] if isinstance(events, dict) else events
    return [event for event in events if isinstance(event, dict)], answer.get("errors") or {}


def is_local(host):
    """Эта же машина — её журналы читаются напрямую, без сети и без лишних прав."""
    host = host.lower()
    return host in ("localhost", "127.0.0.1") or \
        host.split(".")[0] == socket.gethostname().split(".")[0].lower()


def host_picker(monitor, spec):
    """
    Кого опрашивать — по строке вида «dc,FILESRV01»:
    dc — контроллеры домена, all — все компьютеры на связи,
    local — эта машина, остальное — имена компьютеров.
    """
    tokens = [token.strip() for token in spec.split(",") if token.strip()]

    def hosts():
        found = []
        for token in tokens:
            lowered = token.lower()
            if lowered == "dc":
                found += monitor.hosts(dc_only=True)
            elif lowered == "all":
                found += monitor.hosts(online_only=True)
            elif lowered == "local":
                found.append("localhost")
            else:
                found.append(token)
        unique = {}
        for host in found:
            unique.setdefault(host.lower(), host)
        return list(unique.values())

    return hosts


class EventCollector:
    def __init__(self, hosts, state_path, interval=300, hours=24, fetch=fetch,
                 workers=WORKERS, clock=time.time, spec="", address_book=dict):
        self.hosts = hosts
        self.address_book = address_book  # адрес или имя → имя компьютера в таблице
        self.state_path = Path(state_path)
        self.interval = interval
        self.window = hours * 3600
        self.fetch = fetch
        self.workers = workers
        self.clock = clock
        self.spec = spec
        self.events = []
        self.marks = {}     # «компьютер|журнал» → время последнего события
        self.sources = {}   # компьютер → когда опрошен, сколько нового, ошибки
        self.logons = {}    # имя компьютера → последние вошедшие пользователи
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

    def collect_now(self):
        self._wake.set()

    def run_once(self):
        started = self.clock()
        self.busy = True
        try:
            hosts = self.hosts()
            book = self.address_book()
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                results = list(pool.map(lambda host: (host, self._fetch_one(host, started)), hosts))
            with self._lock:
                for host, (events, errors) in results:
                    added = self._store(host, events, book)
                    self.sources[host] = {"checked": self.clock(), "added": added,
                                          "errors": errors}
                self.events.sort(key=lambda event: event["time"])
                del self.events[:-MAX_STORED]
                error = "" if hosts else (
                    "Не с кого собирать журналы: контроллеры домена не найдены. "
                    "Укажите компьютеры ключом --event-hosts.")
                self.cycle = {"started": started, "finished": self.clock(),
                              "hosts": len(hosts), "error": error}
                self._save_state()
        except Exception as exc:  # цикл не должен умирать из-за одной ошибки
            self.cycle = {"started": started, "finished": self.clock(),
                          "error": "%s: %s" % (type(exc).__name__, exc)}
        finally:
            self.busy = False

    def _fetch_one(self, host, now):
        since = {log: self.marks.get("%s|%s" % (host.lower(), log), now - self.window)
                 for log in catalog.QUERIES}
        try:
            return self.fetch(host, since, local=is_local(host))
        except EventLogError as exc:
            return [], {"*": str(exc)}

    def _store(self, host, events, book=None):
        known = {event["key"] for event in self.events}
        added = 0
        for event in events:
            query = event.get("query") or event.get("log")
            key = "%s|%s|%s" % (host.lower(), event.get("log"), event.get("record"))
            mark = "%s|%s" % (host.lower(), query)
            self.marks[mark] = max(self.marks.get(mark, 0), event.get("time") or 0)
            if query == catalog.LOGONS:
                self._remember_logon(host, event, book or {})
                continue
            if key in known:
                continue
            known.add(key)
            event = dict(event, key=key, host=host)
            self.events.append(event)
            added += 1
        return added

    def _remember_logon(self, host, event, book):
        """Вход в домен: с какого компьютера (по IP) и под каким пользователем."""
        data = event.get("data") or {}
        user = (data.get("TargetUserName") or "").strip()
        if not user or user.endswith("$") or user.lower() == "krbtgt":
            return  # учётные записи компьютеров и служебная — это не люди
        address = catalog.DECODERS["ip"](data.get("IpAddress"))
        # С ::1 входят на сам контроллер домена, с которого читаем журнал.
        computer = book.get((host if address in ("::1", "127.0.0.1") else address).lower())
        if not computer:
            return
        when = event.get("time") or 0
        recent = self.logons.get(computer.lower(), [])
        for entry in recent:
            if entry["user"].lower() == user.lower():
                if entry["time"] >= when:
                    return
                recent.remove(entry)
                break
        recent.append({"user": user, "domain": data.get("TargetDomainName") or "",
                       "time": when, "address": address})
        recent.sort(key=lambda entry: entry["time"])
        self.logons[computer.lower()] = recent[-RECENT_USERS:]

    def annotate(self, computers):
        """Дописать в строки таблицы компьютеров, кто входил последним."""
        with self._lock:
            logons = {key: list(value) for key, value in self.logons.items()}
        for record in computers:
            recent = logons.get(record["name"].lower(), [])
            last = recent[-1] if recent else {}
            record.update(user=last.get("user", ""), user_time=last.get("time"),
                          users=list(reversed(recent)))
        return computers

    # ------------------------------------------------------- состояние
    def snapshot(self, limit=1000):
        with self._lock:
            events = [dict(event, **catalog.describe(event))
                      for event in reversed(self.events[-limit:])]
            sources = [dict(info, host=host) for host, info in sorted(self.sources.items())]
            cycle = dict(self.cycle)
        now = self.clock()
        counts = {level: 0 for level in catalog.LEVELS}
        for event in events:
            if now - event["time"] <= 86400:
                counts[event["severity"]] += 1
        return {"enabled": True, "events": events, "counts": counts, "sources": sources,
                "cycle": cycle, "busy": self.busy, "now": now, "interval": self.interval,
                "spec": self.spec, "levels": catalog.LEVELS}

    def _load_state(self):
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.events = [event for event in state.get("events", [])
                       if isinstance(event, dict) and event.get("key")][-MAX_STORED:]
        self.marks = dict(state.get("marks", {}))
        self.sources = dict(state.get("sources", {}))
        self.logons = dict(state.get("logons", {}))

    def _save_state(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps({"events": self.events, "marks": self.marks,
                                    "sources": self.sources, "logons": self.logons},
                                   ensure_ascii=False),
                        encoding="utf-8")
        os.replace(temp, self.state_path)

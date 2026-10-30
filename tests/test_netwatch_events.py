"""Тесты вкладки журналов: справочник событий, запрос к Get-WinEvent, сборщик, страница."""

import base64
import http.client
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netwatch import catalog, eventlogs  # noqa: E402
from netwatch.directory import Computer, computer_from_ad  # noqa: E402
from netwatch.eventlogs import EventCollector, EventLogError, host_picker  # noqa: E402
from netwatch.monitor import Monitor  # noqa: E402
from netwatch.probe import Result  # noqa: E402
from netwatch.server import make_server  # noqa: E402

NOW = 1_800_000_000.0
AUDIT = catalog.AUDIT


def event(record, event_id, data=None, log="Security", provider=AUDIT, ago=60, message=""):
    return {"log": log, "provider": provider, "id": event_id, "record": record, "level": 0,
            "time": NOW - ago, "data": data or {}, "message": message}


def powershell_answer(payload):
    packed = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return ("OK:%s\r\n" % packed).encode("ascii")


class CatalogTest(unittest.TestCase):
    def test_every_event_is_explained(self):
        for info in catalog.CATALOG:
            self.assertIn(info.level, catalog.LEVELS, info.id)
            for text in (info.title, info.meaning, info.action):
                self.assertTrue(text.strip(), info.id)
            for names, label, decoder in info.fields:
                self.assertTrue(names and label, info.id)
                self.assertTrue(decoder is None or decoder in catalog.DECODERS, info.id)

    def test_no_duplicates(self):
        self.assertEqual(len(catalog.BY_KEY), len(catalog.CATALOG))

    def test_query_is_valid_xml_with_all_events(self):
        for log in catalog.LOGS:
            text = catalog.query_xml(log, "2026-10-18T10:00:00.000Z")
            root = ElementTree.fromstring(text)  # не упадёт — значит, XML правильный
            selects = root.findall("./Query/Select")
            self.assertTrue(selects)
            xpaths = " ".join(select.text for select in selects)
            for info in catalog.CATALOG:
                if info.log == log:
                    self.assertIn("EventID=%d" % info.id, xpaths)
                    self.assertIn("@Name='%s'" % info.provider, xpaths)
            self.assertIn(">='2026-10-18T10:00:00.000Z'", xpaths)
            for select in selects:  # у журнала Windows лимит на число условий в запросе
                self.assertLessEqual(select.text.count("EventID="), catalog.MAX_IDS_PER_QUERY)

    def test_ntlm_only_failures(self):
        xpaths = catalog.query_xml("Security", "2026-01-01T00:00:00.000Z")
        select = next(s.text for s in ElementTree.fromstring(xpaths).iter("Select")
                      if "EventID=4776" in s.text)
        self.assertIn("band(Keywords,%d)" % catalog.AUDIT_FAILURE, select)
        self.assertNotIn("EventID=4625", select)  # остальные события фильтром не режутся


class DescribeTest(unittest.TestCase):
    def pairs(self, described):
        return dict(described["summary"])

    def test_failed_logon(self):
        described = catalog.describe(event(1, 4625, {
            "TargetUserName": "ivanova", "WorkstationName": "BUH-PC01",
            "IpAddress": "::ffff:10.1.0.57", "LogonType": "10",
            "Status": "0xC000006D", "SubStatus": "0xC000006A"}))
        self.assertEqual(described["title"], "Неудачный вход в систему")
        self.assertEqual(described["severity"], catalog.WARNING)
        pairs = self.pairs(described)
        self.assertEqual(pairs["IP"], "10.1.0.57")
        self.assertEqual(pairs["Тип входа"], "удалённый рабочий стол (RDP)")
        self.assertEqual(pairs["Причина"], "неверный пароль (0xC000006A)")  # SubStatus точнее
        self.assertTrue(described["meaning"] and described["action"])

    def test_substatus_zero_falls_back_to_status(self):
        pairs = self.pairs(catalog.describe(event(1, 4625, {
            "TargetUserName": "x", "Status": "0xC0000234", "SubStatus": "0x0"})))
        self.assertEqual(pairs["Причина"], "учётная запись заблокирована (0xC0000234)")

    def test_lockout_shows_source_computer(self):
        pairs = self.pairs(catalog.describe(event(1, 4740, {
            "TargetUserName": "ivanova", "TargetDomainName": "BUH-PC01"})))
        self.assertEqual(pairs, {"Учётная запись": "ivanova", "Источник (компьютер)": "BUH-PC01"})

    def test_kerberos_codes(self):
        pairs = self.pairs(catalog.describe(event(1, 4771, {"TargetUserName": "p",
                                                            "Status": "0x25"})))
        self.assertIn("часы", pairs["Причина"])

    def test_admin_group_is_critical(self):
        added = {"MemberName": "CN=Сидоров", "TargetUserName": "Domain Admins",
                 "SubjectUserName": "admin"}
        self.assertEqual(catalog.describe(event(1, 4728, added))["severity"], catalog.CRITICAL)
        russian = dict(added, TargetUserName="Администраторы домена")
        self.assertEqual(catalog.describe(event(1, 4728, russian))["severity"], catalog.CRITICAL)
        plain = dict(added, TargetUserName="Бухгалтерия")
        self.assertEqual(catalog.describe(event(1, 4728, plain))["severity"], catalog.WARNING)
        # Удаление из админской группы тревогой не считается.
        self.assertEqual(catalog.describe(event(1, 4729, added))["severity"], catalog.INFO)

    def test_log_cleared(self):
        described = catalog.describe(event(1, 1102, {"SubjectUserName": "admin2"},
                                           provider="Microsoft-Windows-Eventlog"))
        self.assertEqual(described["severity"], catalog.CRITICAL)
        self.assertEqual(self.pairs(described), {"Кто сделал": "admin2"})

    def test_event_without_fields_uses_message(self):
        described = catalog.describe(event(
            1, 5722, log="System", provider="NETLOGON",
            message="\nСеанс с компьютера SKLAD-01 не прошёл проверку.\nПодробности…"))
        self.assertEqual(described["title"], "DC: компьютер не прошёл проверку")
        self.assertEqual(described["summary"], "Сеанс с компьютера SKLAD-01 не прошёл проверку.")

    def test_same_id_other_provider_is_not_confused(self):
        # Код 7 у диска — сбойный сектор; у другого источника это что-то иное.
        self.assertEqual(catalog.describe(event(1, 7, log="System", provider="disk"))["title"],
                         "Сбойный сектор на диске")
        other = catalog.describe(dict(event(1, 7, log="System", provider="Другое"), level=2))
        self.assertEqual(other["severity"], catalog.ERROR)
        self.assertNotEqual(other["title"], "Сбойный сектор на диске")


class FetchTest(unittest.TestCase):
    def fake_run(self, payload):
        calls = []

        def run(command, **_kwargs):
            calls.append(base64.b64decode(command[-1]).decode("utf-16-le"))
            return subprocess.CompletedProcess(command, 0, powershell_answer(payload), b"")

        return run, calls

    def test_remote_fetch(self):
        run, calls = self.fake_run({"events": [event(5, 4740)], "errors": {"Security": "нет"}})
        since = {log: NOW - 3600 for log in catalog.QUERIES}
        events, errors = eventlogs.fetch("dc01.corp.local", since, run=run)
        self.assertEqual([e["id"] for e in events], [4740])
        self.assertEqual(errors, {"Security": "нет"})
        script = calls[0]
        self.assertIn("$computer = 'dc01.corp.local'", script)
        self.assertIn("Get-WinEvent", script)
        self.assertIn("Provider[@Name=''NETLOGON'']", script)  # кавычки удвоены для PowerShell
        self.assertIn(eventlogs.iso_utc(NOW - 3600), script)

    def test_local_fetch_has_no_computer_name(self):
        run, calls = self.fake_run({"events": [], "errors": {}})
        eventlogs.fetch("DC01", {log: NOW for log in catalog.QUERIES}, local=True, run=run)
        self.assertIn("$computer = ''", calls[0])

    def test_single_event_object(self):
        run, _ = self.fake_run({"events": event(1, 4740), "errors": {}})
        events, _ = eventlogs.fetch("dc", {log: NOW for log in catalog.QUERIES}, run=run)
        self.assertEqual(len(events), 1)

    def test_no_powershell(self):
        def missing(*_args, **_kwargs):
            raise FileNotFoundError("powershell")

        with self.assertRaises(EventLogError) as caught:
            eventlogs.fetch("dc", {log: NOW for log in catalog.QUERIES}, run=missing)
        self.assertIn("Windows", str(caught.exception))

    def test_is_local(self):
        self.assertTrue(eventlogs.is_local("localhost"))
        self.assertTrue(eventlogs.is_local("127.0.0.1"))
        self.assertFalse(eventlogs.is_local("dc01.corp.local"))


class HostPickerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        alive = {"dc01.corp.local", "pc01.corp.local"}
        computers = [Computer("DC01", "dc01.corp.local", is_dc=True),
                     Computer("DC02", "dc02.corp.local", is_dc=True, enabled=False),
                     Computer("PC01", "pc01.corp.local"), Computer("PC02", "pc02.corp.local")]
        self.monitor = Monitor(lambda: computers, Path(self.tmp.name) / "state.json",
                               probe=lambda host: Result(host in alive, "", ""),
                               clock=lambda: NOW)
        self.monitor.run_once()

    def tearDown(self):
        self.tmp.cleanup()

    def test_domain_controllers(self):
        self.assertEqual(host_picker(self.monitor, "dc")(), ["dc01.corp.local"])

    def test_all_online(self):
        self.assertEqual(sorted(host_picker(self.monitor, "all")()),
                         ["dc01.corp.local", "pc01.corp.local"])

    def test_mixed_without_duplicates(self):
        self.assertEqual(host_picker(self.monitor, "dc, DC01.corp.local, FILESRV, local")(),
                         ["dc01.corp.local", "FILESRV", "localhost"])

    def test_dc_flag_from_ad(self):
        # 532480 = 0x82000: так userAccountControl выглядит у контроллера домена.
        self.assertTrue(computer_from_ad({"name": "DC01", "useraccountcontrol": "532480"}).is_dc)
        self.assertFalse(computer_from_ad({"name": "PC01", "useraccountcontrol": "4096"}).is_dc)


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "eventlog.json"
        self.now = NOW
        self.remote = {"dc01": [event(1, 4740, {"TargetUserName": "ivanova"}, ago=120),
                                event(2, 4625, {"TargetUserName": "ivanova"}, ago=180)]}
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, host, since, local=False):
        self.calls.append((host, dict(since)))
        if host == "dc02":
            raise EventLogError("Доступ запрещён")
        return list(self.remote.get(host, [])), {}

    def collector(self, hosts=("dc01", "dc02")):
        return EventCollector(lambda: list(hosts), self.state, fetch=self.fetch,
                              clock=lambda: self.now, hours=24)

    def test_collects_and_explains(self):
        collector = self.collector()
        collector.run_once()
        snapshot = collector.snapshot()
        self.assertEqual([e["id"] for e in snapshot["events"]], [4740, 4625])  # новые сверху
        self.assertEqual(snapshot["events"][0]["title"], "Учётная запись заблокирована")
        self.assertEqual(snapshot["events"][0]["host"], "dc01")
        self.assertEqual(snapshot["counts"][catalog.ERROR], 1)
        self.assertEqual(snapshot["counts"][catalog.WARNING], 1)
        sources = {s["host"]: s for s in snapshot["sources"]}
        self.assertEqual(sources["dc01"]["added"], 2)
        self.assertIn("Доступ запрещён", sources["dc02"]["errors"]["*"])

    def test_first_window_then_only_new(self):
        collector = self.collector(hosts=("dc01",))
        collector.run_once()
        self.assertEqual(self.calls[0][1]["Security"], NOW - 24 * 3600)
        self.now += 300
        collector.run_once()  # Get-WinEvent вернул то же самое — дублей быть не должно
        self.assertEqual(len(collector.snapshot()["events"]), 2)
        self.assertEqual(self.calls[1][1]["Security"], NOW - 120)  # с последнего события
        self.assertEqual(self.calls[1][1]["System"], self.now - 24 * 3600)  # в System пусто

    def test_survives_restart(self):
        self.collector(hosts=("dc01",)).run_once()
        again = self.collector(hosts=("dc01",))
        self.assertEqual(len(again.snapshot()["events"]), 2)
        again.run_once()
        self.assertEqual(len(again.snapshot()["events"]), 2)
        self.assertEqual(self.calls[-1][1]["Security"], NOW - 120)

    def test_no_hosts_is_explained(self):
        collector = self.collector(hosts=())
        collector.run_once()
        self.assertIn("--event-hosts", collector.snapshot()["cycle"]["error"])

    def test_old_events_not_counted_for_today(self):
        self.remote["dc01"].append(event(3, 1102, provider="Microsoft-Windows-Eventlog",
                                         ago=3 * 86400))
        collector = self.collector(hosts=("dc01",))
        collector.run_once()
        snapshot = collector.snapshot()
        self.assertEqual(len(snapshot["events"]), 3)
        self.assertEqual(snapshot["counts"][catalog.CRITICAL], 0)


class EventServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        self.monitor = Monitor(lambda: [Computer("DC01", is_dc=True)], folder / "state.json",
                               probe=lambda host: Result(True, "10.0.0.1", "TCP 445"),
                               clock=lambda: NOW)
        self.monitor.run_once()
        fetch = lambda host, since, local=False: (  # noqa: E731
            [event(1, 4740, {"TargetUserName": "ivanova", "TargetDomainName": "PC01"})], {})
        self.collector = EventCollector(host_picker(self.monitor, "dc"), folder / "ev.json",
                                        fetch=fetch, clock=lambda: NOW)
        self.collector.run_once()
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, collector=True):
        server = make_server(self.monitor, port=0, collector=self.collector if collector else None)
        self.servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request(method, path)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_events(self):
        status, body = self.request("GET", "/api/eventlog")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["events"][0]["title"], "Учётная запись заблокирована")
        self.assertIn(["Источник (компьютер)", "PC01"], payload["events"][0]["summary"])

    def test_reference(self):
        status, body = self.request("GET", "/api/eventlog/catalog")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(len(payload["events"]), len(catalog.CATALOG))
        self.assertTrue(all(item["meaning"] for item in payload["events"]))

    def test_csv(self):
        status, body = self.request("GET", "/api/eventlog.csv")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertTrue(text.startswith("\ufeffВремя;Важность;Компьютер;"))
        self.assertIn("Учётная запись заблокирована;"
                      "Учётная запись: ivanova · Источник (компьютер): PC01", text)

    def test_collect_now(self):
        self.assertEqual(self.request("POST", "/api/eventlog/collect")[0], 202)
        self.assertTrue(self.collector._wake.is_set())

    def test_disabled(self):
        status, body = self.request("GET", "/api/eventlog", collector=False)
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["enabled"])
        self.assertEqual(self.request("POST", "/api/eventlog/collect", collector=False)[0], 404)

    def test_page_has_events_tab(self):
        _status, body = self.request("GET", "/")
        self.assertIn("Журналы событий", body.decode("utf-8"))



def logon(record, user, address, ago=60, domain="CORP"):
    return dict(event(record, 4768, {"TargetUserName": user, "TargetDomainName": domain,
                                     "IpAddress": address}, ago=ago), query=catalog.LOGONS)


class LastUserTest(unittest.TestCase):
    """Столбец «Пользователь»: кто входил на компьютер, по событию 4768 на DC."""

    BOOK = {"pc01": "PC01", "pc01.corp.local": "PC01", "10.1.0.57": "PC01",
            "10.1.0.80": "PC02", "dc01.corp.local": "DC01"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.remote = []
        self.collector = EventCollector(
            lambda: ["dc01.corp.local"], Path(self.tmp.name) / "eventlog.json",
            fetch=lambda host, since, local=False: (list(self.remote), {}),
            clock=lambda: NOW, address_book=lambda: dict(self.BOOK))

    def tearDown(self):
        self.tmp.cleanup()

    def users(self, name):
        rows = self.collector.annotate([{"name": name}])
        return rows[0]

    def test_query_takes_only_successful_logons(self):
        text = catalog.logons_xml("2026-10-18T10:00:00.000Z")
        select = ElementTree.fromstring(text).find("./Query/Select")
        self.assertEqual(select.get("Path"), "Security")
        self.assertIn("EventID=4768", select.text)
        self.assertIn("band(Keywords,%d)" % catalog.AUDIT_SUCCESS, select.text)

    def test_logons_are_a_separate_query(self):
        calls = []

        def run(command, **_kwargs):
            calls.append(base64.b64decode(command[-1]).decode("utf-16-le"))
            return subprocess.CompletedProcess(command, 0,
                                               powershell_answer({"events": [], "errors": {}}), b"")

        eventlogs.fetch("dc01", {name: NOW for name in catalog.QUERIES}, run=run)
        self.assertIn("log = 'Logons'", calls[0])
        self.assertIn("max = %d; message = $false" % eventlogs.MAX_LOGONS, calls[0])
        self.assertIn("max = %d; message = $true" % eventlogs.MAX_PER_LOG, calls[0])

    def test_last_user_by_address(self):
        self.remote = [logon(1, "ivanova", "::ffff:10.1.0.57", ago=600),
                       logon(2, "petrov", "::ffff:10.1.0.80", ago=300)]
        self.collector.run_once()
        pc01 = self.users("PC01")
        self.assertEqual(pc01["user"], "ivanova")
        self.assertEqual(pc01["user_time"], NOW - 600)
        self.assertEqual(self.users("PC02")["user"], "petrov")
        # Входы — не события для списка: их тысячи в день.
        self.assertEqual(self.collector.snapshot()["events"], [])

    def test_newest_wins_whatever_the_order(self):
        # Get-WinEvent отдаёт новые первыми.
        self.remote = [logon(3, "petrov", "10.1.0.57", ago=60),
                       logon(2, "ivanova", "10.1.0.57", ago=600),
                       logon(1, "petrov", "10.1.0.57", ago=3600)]
        self.collector.run_once()
        row = self.users("PC01")
        self.assertEqual(row["user"], "petrov")
        self.assertEqual([(u["user"], u["time"]) for u in row["users"]],
                         [("petrov", NOW - 60), ("ivanova", NOW - 600)])

    def test_keeps_only_recent_users(self):
        self.remote = [logon(i, "user%d" % i, "10.1.0.57", ago=1000 - i) for i in range(8)]
        self.collector.run_once()
        names = [u["user"] for u in self.users("PC01")["users"]]
        self.assertEqual(names, ["user7", "user6", "user5", "user4", "user3"])

    def test_computer_accounts_and_unknown_addresses_skipped(self):
        self.remote = [logon(1, "PC01$", "10.1.0.57", ago=10),
                       logon(2, "krbtgt", "10.1.0.57", ago=20),
                       logon(3, "guest", "10.9.9.9", ago=30),
                       logon(4, "ivanova", "10.1.0.57", ago=40)]
        self.collector.run_once()
        self.assertEqual(self.users("PC01")["user"], "ivanova")
        self.assertEqual(len(self.users("PC01")["users"]), 1)

    def test_logon_on_the_dc_itself(self):
        self.remote = [logon(1, "admin", "::1")]
        self.collector.run_once()
        self.assertEqual(self.users("DC01")["user"], "admin")

    def test_unknown_computer_has_no_user(self):
        row = self.users("NOBODY")
        self.assertEqual((row["user"], row["user_time"], row["users"]), ("", None, []))

    def test_mark_and_restart(self):
        self.remote = [logon(1, "ivanova", "10.1.0.57", ago=120)]
        self.collector.run_once()
        self.assertEqual(self.collector.marks["dc01.corp.local|Logons"], NOW - 120)
        again = EventCollector(lambda: [], Path(self.tmp.name) / "eventlog.json",
                               fetch=lambda host, since, local=False: ([], {}),
                               clock=lambda: NOW)
        self.assertEqual(again.annotate([{"name": "PC01"}])[0]["user"], "ivanova")

    def test_status_page_shows_user(self):
        monitor = Monitor(lambda: [Computer("PC01", "pc01.corp.local")],
                          Path(self.tmp.name) / "state.json",
                          probe=lambda host: Result(True, "10.1.0.57", "TCP 445"),
                          clock=lambda: NOW)
        monitor.run_once()
        self.assertEqual(monitor.address_book()["10.1.0.57"], "PC01")
        self.remote = [logon(1, "ivanova", "::ffff:10.1.0.57")]
        self.collector.address_book = monitor.address_book
        self.collector.run_once()
        server = make_server(monitor, port=0, collector=self.collector)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1],
                                                    timeout=5)
            connection.request("GET", "/api/status")
            payload = json.loads(connection.getresponse().read())
            connection.request("GET", "/api/export.csv")
            text = connection.getresponse().read().decode("utf-8")
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(payload["computers"][0]["user"], "ivanova")
        self.assertIn("PC01;ivanova;", text)


if __name__ == "__main__":
    unittest.main()

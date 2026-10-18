"""Тесты мониторинга ПК: разбор ответа AD, статусы, состояние, веб-страница."""

import base64
import http.client
import json
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netwatch import directory, probe  # noqa: E402
from netwatch.directory import Computer, DirectoryError  # noqa: E402
from netwatch.monitor import (DAY, DISABLED, DOMAIN, LOST, OFFLINE, ONLINE,  # noqa: E402
                              Monitor, classify)
from netwatch.probe import Result  # noqa: E402
from netwatch.server import make_server  # noqa: E402

NOW = 1_800_000_000.0


def filetime_of(stamp):
    return str(int((stamp + directory.FILETIME_EPOCH) * 10_000_000))


def powershell_answer(prefix, payload):
    # Так отвечает AD_SCRIPT: мусор от PowerShell, потом строка с base64.
    packed = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    return ("#< CLIXML\r\n%s:%s\r\n" % (prefix, packed)).encode("ascii")


class DirectoryTest(unittest.TestCase):
    def test_filetime(self):
        self.assertAlmostEqual(directory.filetime(filetime_of(NOW)), NOW, places=3)
        self.assertIsNone(directory.filetime("0"))
        self.assertIsNone(directory.filetime(str(directory.FILETIME_NEVER)))
        self.assertIsNone(directory.filetime(None))
        self.assertIsNone(directory.filetime("мусор"))

    def test_ou_from_dn(self):
        self.assertEqual(directory.ou_from_dn(
            "CN=PC01,OU=Бухгалтерия,OU=Ашхабад,DC=corp,DC=local"), "Ашхабад/Бухгалтерия")
        self.assertEqual(directory.ou_from_dn("CN=PC02,CN=Computers,DC=corp,DC=local"),
                         "Computers")
        self.assertEqual(directory.ou_from_dn(r"CN=PC03,OU=Склад\, цех 2,DC=corp,DC=local"),
                         "Склад, цех 2")
        self.assertEqual(directory.ou_from_dn(""), "")

    def test_parse_ad_rows(self):
        rows = [{"name": "PC01", "dnshostname": "pc01.corp.local",
                 "operatingsystem": "Windows 11 Pro", "useraccountcontrol": "4096",
                 "lastlogontimestamp": filetime_of(NOW - DAY), "pwdlastset": filetime_of(NOW),
                 "distinguishedname": "CN=PC01,OU=Офис,DC=corp,DC=local",
                 "description": "Бухгалтерия, Иванова"},
                {"name": "OLD", "useraccountcontrol": "4098"},
                {"dnshostname": "без-имени"}, None]
        computers = directory.parse_ad_output(powershell_answer("OK", json.dumps(rows)))
        self.assertEqual([c.name for c in computers], ["PC01", "OLD"])
        pc = computers[0]
        self.assertEqual(pc.host, "pc01.corp.local")
        self.assertEqual(pc.ou, "Офис")
        self.assertEqual(pc.description, "Бухгалтерия, Иванова")
        self.assertTrue(pc.enabled)
        self.assertAlmostEqual(pc.last_logon, NOW - DAY, places=3)
        old = computers[1]
        self.assertFalse(old.enabled)
        self.assertEqual(old.host, "OLD")  # нет DNS-имени — проверяем по имени

    def test_ad_error_is_readable(self):
        with self.assertRaises(DirectoryError) as caught:
            directory.parse_ad_output(powershell_answer("ERR", "Сервер не работает"))
        self.assertIn("Сервер не работает", str(caught.exception))

    def test_empty_output(self):
        with self.assertRaises(DirectoryError):
            directory.parse_ad_output(b"")

    def test_garbled_output(self):
        with self.assertRaises(DirectoryError):
            directory.parse_ad_output(b"OK:eyJuYW1l")  # обрезано на полуслове

    def test_runs_powershell_with_encoded_script(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, powershell_answer("OK", "[]"), b"")

        self.assertEqual(directory.from_active_directory("OU=It's,DC=corp", run=fake_run), [])
        command = calls[0]
        self.assertEqual(command[0], "powershell")
        script = base64.b64decode(command[-1]).decode("utf-16-le")
        self.assertIn("$base = 'OU=It''s,DC=corp'", script)  # кавычка экранирована
        self.assertIn("lastlogontimestamp", script)

    def test_no_powershell(self):
        def missing(*_args, **_kwargs):
            raise FileNotFoundError("powershell")

        with self.assertRaises(DirectoryError) as caught:
            directory.from_active_directory(run=missing)
        self.assertIn("--computers", str(caught.exception))

    def test_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pcs.txt"
            path.write_text("# список\nPC01\n\nPC02 10.0.0.2  # склад\nPC03;pc03.corp.local\n",
                            encoding="utf-8-sig")
            computers = directory.from_file(path)
        self.assertEqual([(c.name, c.host) for c in computers],
                         [("PC01", "PC01"), ("PC02", "10.0.0.2"), ("PC03", "pc03.corp.local")])

    def test_missing_file(self):
        with self.assertRaises(DirectoryError):
            directory.from_file("/нет/такого/файла.txt")


class ClassifyTest(unittest.TestCase):
    LOST, STALE = 3 * DAY, 45 * DAY

    def status(self, **record):
        return classify(dict({"enabled": True}, **record), NOW, self.LOST, self.STALE)

    def test_online(self):
        self.assertEqual(self.status(online=True, last_logon=NOW - DAY), ONLINE)

    def test_online_without_ad_data(self):
        # Список из файла: дат входа в домен нет — это не «нет связи с доменом».
        self.assertEqual(self.status(online=True), ONLINE)

    def test_broken_trust(self):
        self.assertEqual(self.status(online=True, last_logon=NOW - 60 * DAY), DOMAIN)
        self.assertEqual(self.status(online=True, last_logon=NOW - DAY,
                                     pwd_last_set=NOW - 90 * DAY), DOMAIN)

    def test_offline_recently(self):
        self.assertEqual(self.status(online=False, last_seen=NOW - 3600), OFFLINE)

    def test_lost(self):
        self.assertEqual(self.status(online=False, last_seen=NOW - 5 * DAY), LOST)
        self.assertEqual(self.status(online=False), LOST)  # ни разу не видели

    def test_first_run_uses_ad_logon(self):
        self.assertEqual(self.status(online=False, last_logon=NOW - DAY), OFFLINE)
        self.assertEqual(self.status(online=False, last_logon=NOW - 10 * DAY), LOST)

    def test_disabled_wins(self):
        self.assertEqual(self.status(enabled=False, online=True), DISABLED)


class FakeNetwork:
    """Кто сейчас «включён» — решает тест."""

    def __init__(self):
        self.alive = set()
        self.checked = []

    def __call__(self, host):
        self.checked.append(host)
        if host in self.alive:
            return Result(True, "10.0.0.%d" % (len(host) % 250), "TCP 445")
        return Result(False, "", "не отвечает")


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"
        self.now = NOW
        self.computers = [Computer("PC01", last_logon=NOW - DAY),
                          Computer("PC02", last_logon=NOW - DAY),
                          Computer("OLD", enabled=False)]
        self.network = FakeNetwork()
        self.network.alive = {"PC01", "PC02"}

    def tearDown(self):
        self.tmp.cleanup()

    def monitor(self, load=None):
        return Monitor(load or (lambda: list(self.computers)), self.state,
                       probe=self.network, clock=lambda: self.now, workers=4)

    def statuses(self, monitor):
        return {rec["name"]: rec["status"] for rec in monitor.snapshot()["computers"]}

    def test_first_pass(self):
        monitor = self.monitor()
        monitor.run_once()
        self.assertEqual(self.statuses(monitor),
                         {"PC01": ONLINE, "PC02": ONLINE, "OLD": DISABLED})
        self.assertNotIn("OLD", self.network.checked)  # отключённые не проверяем
        self.assertEqual(monitor.snapshot()["events"], [])  # первый проход — без шума
        self.assertEqual(monitor.snapshot()["counts"][ONLINE], 2)

    def test_one_miss_is_forgiven(self):
        monitor = self.monitor()
        monitor.run_once()
        self.network.alive = {"PC01"}
        self.now += 120
        monitor.run_once()
        self.assertEqual(self.statuses(monitor)["PC02"], ONLINE)
        self.now += 120
        monitor.run_once()
        self.assertEqual(self.statuses(monitor)["PC02"], OFFLINE)
        event = monitor.snapshot()["events"][0]
        self.assertEqual((event["name"], event["from"], event["to"]), ("PC02", ONLINE, OFFLINE))

    def test_goes_lost_then_comes_back(self):
        monitor = self.monitor()
        monitor.run_once()
        self.network.alive = {"PC01"}
        for _ in range(2):
            self.now += 120
            monitor.run_once()
        self.now += 4 * DAY
        monitor.run_once()
        self.assertEqual(self.statuses(monitor)["PC02"], LOST)
        self.network.alive = {"PC01", "PC02"}
        self.now += 120
        monitor.run_once()
        self.assertEqual(self.statuses(monitor)["PC02"], ONLINE)
        transitions = [(e["from"], e["to"]) for e in reversed(monitor.snapshot()["events"])]
        self.assertEqual(transitions, [(ONLINE, OFFLINE), (OFFLINE, LOST), (LOST, ONLINE)])

    def test_state_survives_restart(self):
        monitor = self.monitor()
        monitor.run_once()
        last_seen = monitor.records["pc01"]["last_seen"]
        self.network.alive = set()
        again = self.monitor(load=lambda: (_ for _ in ()).throw(DirectoryError("DC недоступен")))
        self.assertEqual(again.records["pc01"]["last_seen"], last_seen)
        # AD недоступен — работаем по старому списку и показываем ошибку.
        self.now += 120
        again.run_once()
        self.now += 120
        again.run_once()
        self.assertEqual(self.statuses(again)["PC01"], OFFLINE)
        self.assertIn("DC недоступен", again.snapshot()["cycle"]["error"])

    def test_added_and_removed(self):
        monitor = self.monitor()
        monitor.run_once()
        self.computers = [Computer("PC01"), Computer("NEW")]
        self.now += 120
        monitor.run_once()
        names = set(self.statuses(monitor))
        self.assertEqual(names, {"PC01", "NEW"})
        notes = {(e["name"], e["note"]) for e in monitor.snapshot()["events"]}
        self.assertIn(("NEW", "появился в списке"), notes)
        self.assertIn(("PC02", "удалён из списка"), notes)

    def test_probe_crash_does_not_kill_loop(self):
        def broken(_host):
            raise RuntimeError("сломалось")

        monitor = Monitor(lambda: self.computers, self.state, probe=broken,
                          clock=lambda: self.now)
        monitor.run_once()
        self.assertIn("сломалось", monitor.snapshot()["cycle"]["error"])
        self.assertFalse(monitor.busy)


class ProbeTest(unittest.TestCase):
    def test_listening_port_is_alive(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            self.assertTrue(probe.tcp_alive("127.0.0.1", listener.getsockname()[1]))

    def test_refused_port_still_means_host_is_up(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]  # порт заняли и не слушаем — будет отказ
            self.assertTrue(probe.tcp_alive("127.0.0.1", port, timeout=5))  # Windows повторяет SYN после RST

    def test_check_localhost(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            result = probe.check("localhost", ports=(port,))
        self.assertTrue(result.alive)
        self.assertEqual(result.address, "127.0.0.1")
        self.assertEqual(result.how, "TCP %d" % port)

    def test_unknown_name(self):
        result = probe.check("нет-такого-компьютера.invalid", ports=())
        self.assertFalse(result.alive)
        self.assertEqual(result.how, "нет в DNS")


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        network = FakeNetwork()
        network.alive = {"PC01"}
        self.monitor = Monitor(lambda: [Computer("PC01", os="Windows 11", ou="Офис"),
                                        Computer("PC02", description="склад")],
                               Path(self.tmp.name) / "state.json", probe=network,
                               clock=lambda: NOW)
        self.monitor.run_once()
        self.server = make_server(self.monitor, port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_page(self):
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Мониторинг ПК в домене", body.decode("utf-8"))

    def test_status(self):
        status, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        statuses = {rec["name"]: rec["status"] for rec in payload["computers"]}
        self.assertEqual(statuses, {"PC01": ONLINE, "PC02": LOST})
        self.assertEqual(payload["counts"][ONLINE], 1)

    def test_csv_opens_in_russian_excel(self):
        status, body = self.request("GET", "/api/export.csv")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertTrue(text.startswith("\ufeffКомпьютер;Статус;"))
        self.assertIn("PC01;На связи;", text)
        self.assertIn("PC02;Пропал;", text)

    def test_check_now(self):
        status, _ = self.request("POST", "/api/check")
        self.assertEqual(status, 202)
        self.assertTrue(self.monitor._wake.is_set())

    def test_unknown_path(self):
        self.assertEqual(self.request("GET", "/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()

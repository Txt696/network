"""Тесты карты сети: разбор связей и ответы сервера."""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netcore import Device, Vault, links  # noqa: E402
from netweb.server import make_server  # noqa: E402

PASSWORD = "test-master-password"
SECRET = "очень-секретный-пароль"


class LinkParsingTest(unittest.TestCase):
    def test_full_link(self):
        self.assertEqual(links.parse("Gi1/0/48 -> balkan-sw-01:Gi1/0/1"),
                         ("Gi1/0/48", "balkan-sw-01", "Gi1/0/1"))

    def test_peer_only(self):
        self.assertEqual(links.parse("balkan-sw-01"), ("", "balkan-sw-01", ""))

    def test_peer_with_port(self):
        self.assertEqual(links.parse("balkan-sw-01:Gi1/0/1"),
                         ("", "balkan-sw-01", "Gi1/0/1"))

    def test_unicode_arrow_and_spaces(self):
        self.assertEqual(links.parse("  Te1/1 → core:Te0/0  "),
                         ("Te1/1", "core", "Te0/0"))

    def test_empty(self):
        self.assertEqual(links.parse(""), ("", "", ""))
        self.assertEqual(links.parse(None), ("", "", ""))

    def test_format_roundtrip(self):
        text = links.format("Gi1/0/48", "balkan-sw-01", "Gi1/0/1")
        self.assertEqual(text, "Gi1/0/48 -> balkan-sw-01:Gi1/0/1")
        self.assertEqual(links.parse(text), ("Gi1/0/48", "balkan-sw-01", "Gi1/0/1"))

    def test_describe(self):
        self.assertEqual(links.describe("Gi1/0/48", "Gi1/0/1"), "Gi1/0/48 — Gi1/0/1")
        self.assertEqual(links.describe("Gi1/0/48", ""), "Gi1/0/48")
        self.assertEqual(links.describe("", ""), "")


class VaultLinksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Vault.create(Path(self.tmp.name) / "vault", PASSWORD)
        self.ashgabat = Device(name="ashgabat-sw-01", kind="switch", site="Ашхабад",
                               mgmt_ip="10.1.0.1", vendor="cisco",
                               uplinks=["Gi1/0/48 -> balkan-sw-01:Gi1/0/1"])
        self.balkan = Device(name="balkan-sw-01", kind="switch", site="Балкан",
                             mgmt_ip="10.2.0.1", vendor="cisco")
        self.vault.save(self.ashgabat)
        self.vault.save(self.balkan)

    def tearDown(self):
        self.tmp.cleanup()

    def test_links_carry_ports(self):
        link = self.vault.links()[0]
        self.assertEqual(link["source"], "ashgabat-sw-01")
        self.assertEqual(link["target"], "balkan-sw-01")
        self.assertEqual(link["local_port"], "Gi1/0/48")
        self.assertEqual(link["peer_port"], "Gi1/0/1")
        self.assertTrue(link["found"])

    def test_topology_still_returns_triples(self):
        self.assertEqual(self.vault.topology(),
                         [("ashgabat-sw-01", "balkan-sw-01", True)])

    def test_unknown_peer_is_marked(self):
        device = Device(name="edge", kind="router", uplinks=["Te0/1 -> нет-такого:Te0/2"])
        self.vault.save(device)
        unknown = [l for l in self.vault.links() if l["source"] == "edge"][0]
        self.assertFalse(unknown["found"])
        self.assertEqual(unknown["target"], "нет-такого")

    def test_backlinks_understand_ports(self):
        self.assertEqual(self.vault.backlinks("balkan-sw-01"), ["ashgabat-sw-01"])


class ServerTest(unittest.TestCase):
    """Сервер поднимается по-настоящему и отвечает по HTTP."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Vault.create(Path(self.tmp.name) / "vault", PASSWORD)
        device = Device(name="ashgabat-sw-01", kind="switch", site="Ашхабад",
                        mgmt_ip="10.1.0.1", vendor="cisco",
                        uplinks=["Gi1/0/48 -> balkan-sw-01:Gi1/0/1"])
        self.vault.save(device)
        self.vault.save(Device(name="balkan-sw-01", kind="switch", site="Балкан",
                               mgmt_ip="10.2.0.1"))
        self.vault.secrets.put(device.secret_ref, username="admin", password=SECRET)

        self.server = make_server(self.vault, port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", quote(path))
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        connection.close()
        return response.status, body

    def test_page_is_served(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Карта сети", body)

    def test_map_has_sites_and_links(self):
        status, body = self.get("/api/map")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual([site["name"] for site in payload["sites"]],
                         ["Ашхабад", "Балкан"])
        self.assertEqual(payload["links"][0]["local_port"], "Gi1/0/48")
        self.assertEqual(payload["links"][0]["peer_port"], "Gi1/0/1")

    def test_device_card(self):
        status, body = self.get("/api/device/ashgabat-sw-01")
        self.assertEqual(status, 200)
        card = json.loads(body)
        self.assertEqual(card["name"], "ashgabat-sw-01")
        self.assertEqual(card["site"], "Ашхабад")
        self.assertEqual(len(card["links"]), 1)

    def test_unknown_device_is_404(self):
        status, _body = self.get("/api/device/нет-такого")
        self.assertEqual(status, 404)

    def test_unknown_path_is_404(self):
        self.assertEqual(self.get("/секреты")[0], 404)

    def test_no_passwords_anywhere(self):
        """Главное: карта не отдаёт пароли, даже если хранилище открыто."""
        self.vault.unlock(PASSWORD)
        for path in ("/", "/api/map", "/api/device/ashgabat-sw-01"):
            _status, body = self.get(path)
            self.assertNotIn(SECRET, body, path)
            self.assertNotIn("password", body.lower(), path)


if __name__ == "__main__":
    unittest.main()

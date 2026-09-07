"""Тесты синхронизации аплинков: линк на одном устройстве появляется у другого."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netcore import Device, Vault  # noqa: E402

PASSWORD = "test-master-password"


class LinkSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Vault.create(Path(self.tmp.name) / "vault", PASSWORD)
        self.vault.save(Device(name="switch1", kind="switch", mgmt_ip="10.0.0.1",
                               ports=["Gi1/0/1-4"]))
        self.vault.save(Device(name="switch2", kind="switch", mgmt_ip="10.0.0.2",
                               ports=["Gi1/0/1-12"]))

    def tearDown(self):
        self.tmp.cleanup()

    def _link(self, device_id, uplinks):
        device = self.vault.get(device_id)
        device.uplinks = uplinks
        self.vault.save(device)
        return self.vault.get(device_id)

    def test_link_appears_on_the_other_side(self):
        self._link("switch1", ["Gi1/0/1 -> switch2:Gi1/0/10"])
        peer = self.vault.get("switch2")
        self.assertEqual(peer.uplinks, ["Gi1/0/10 -> switch1:Gi1/0/1"])

    def test_removing_link_removes_reflection(self):
        self._link("switch1", ["Gi1/0/1 -> switch2:Gi1/0/10"])
        self._link("switch1", [])
        peer = self.vault.get("switch2")
        self.assertEqual(peer.uplinks, [])

    def test_does_not_overwrite_a_conflicting_link(self):
        self.vault.save(Device(name="switch3", kind="switch", mgmt_ip="10.0.0.3"))
        self._link("switch2", ["Gi1/0/10 -> switch3:Gi1/0/1"])
        self._link("switch1", ["Gi1/0/1 -> switch2:Gi1/0/10"])
        peer = self.vault.get("switch2")
        self.assertEqual(peer.uplinks, ["Gi1/0/10 -> switch3:Gi1/0/1"])

    def test_moving_peer_port_migrates_the_reflection(self):
        self._link("switch1", ["Gi1/0/1 -> switch2:Gi1/0/10"])
        self._link("switch1", ["Gi1/0/1 -> switch2:Gi1/0/11"])
        peer = self.vault.get("switch2")
        self.assertEqual(peer.uplinks, ["Gi1/0/11 -> switch1:Gi1/0/1"])

    def test_resaving_an_already_synced_link_is_a_no_op(self):
        self._link("switch1", ["Gi1/0/2 -> switch2:Gi1/0/2"])
        peer = self.vault.get("switch2")
        peer.uplinks.append("Gi1/0/2 -> switch1:Gi1/0/2")
        self.vault.save(peer)
        device = self.vault.get("switch1")
        self.assertEqual(device.uplinks.count("Gi1/0/2 -> switch2:Gi1/0/2"), 1)

    def test_unresolved_peer_is_left_alone(self):
        # Сосед ещё не существует в хранилище — просто нечего синхронизировать.
        device = Device(name="edge", kind="router",
                        uplinks=["Te0/1 -> нет-такого:Te0/2"])
        self.vault.save(device)  # не должно падать
        self.assertIsNone(self.vault.get("нет-такого"))

    def test_portless_uplink_is_not_synced(self):
        # Без порта соседа синхронизировать нечего — и это не ошибка.
        self._link("switch1", ["switch2"])
        peer = self.vault.get("switch2")
        self.assertEqual(peer.uplinks, [])

    def test_reflected_link_survives_a_read_write_roundtrip(self):
        self._link("switch1", ["Gi1/0/1 -> switch2:Gi1/0/10"])
        peer = self.vault.get("switch2")
        # Пересохранить соседа без изменений — линк не должен размножиться.
        self.vault.save(peer)
        peer_again = self.vault.get("switch2")
        self.assertEqual(peer_again.uplinks, ["Gi1/0/10 -> switch1:Gi1/0/1"])


if __name__ == "__main__":
    unittest.main()

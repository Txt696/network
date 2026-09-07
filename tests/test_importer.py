"""Тесты разбора конфигурации Cisco в устройство (netcore/importer.py)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netcore.importer import parse_config  # noqa: E402
from netcore.models import Device  # noqa: E402

SWITCH_CONFIG = """\
hostname BFN-AS-ME-SB-sw
!
switch 1 provision ws-c3560cx-12pd-s
spanning-tree mode rapid-pvst
!
interface Port-channel1
 description For SB-AS-Master-rt
 switchport trunk allowed vlan 1
 switchport mode trunk
!
interface GigabitEthernet1/0/1
 description To SB-AS-Master-rt Cross-1
 switchport trunk allowed vlan 1
 switchport mode trunk
 channel-group 1 mode active
!
interface GigabitEthernet1/0/2
 shutdown
!
interface GigabitEthernet1/0/10
 description To MGM_NET1
 switchport access vlan 100
 switchport mode access
!
interface GigabitEthernet1/0/16
 description To Turkmenbashy-Bank
 switchport trunk allowed vlan 1,13
 switchport mode trunk
!
interface Vlan1
 ip address 10.240.40.4 255.255.0.0
!
end
"""

ROUTER_CONFIG = """\
hostname SB-AS-Master-rt
!
boot system flash:isr4400-universalk9.17.06.05.SPA.bin
!
interface Port-channel1
 description To BFN-AS-ME
 ip address 10.240.40.2 255.255.0.0
!
interface GigabitEthernet0/0/0
 no ip address
 channel-group 1 mode active
!
interface GigabitEthernet0/1/0
 description BFN_TEL
 switchport access vlan 302
 switchport mode access
!
router eigrp 1
 network 10.4.5.0 0.0.0.255
!
end
"""

NO_HOSTNAME_CONFIG = """\
!
line con 0
!
end
"""


class SwitchImportTest(unittest.TestCase):
    def setUp(self):
        self.device, self.hints = parse_config(SWITCH_CONFIG)

    def test_hostname_and_kind(self):
        self.assertEqual(self.device.name, "BFN-AS-ME-SB-sw")
        self.assertEqual(self.device.kind, "switch")
        self.assertEqual(self.device.vendor, "cisco")

    def test_mgmt_ip_from_vlan1(self):
        self.assertEqual(self.device.mgmt_ip, "10.240.40.4")

    def test_ports_grouped_including_port_channel(self):
        self.assertIn("Po1", self.device.ports)
        self.assertIn("Gi1/0/1-2", self.device.ports)
        self.assertIn("Gi1/0/10", self.device.ports)
        self.assertIn("Gi1/0/16", self.device.ports)

    def test_access_and_trunk_vlans_parsed(self):
        self.assertIn("Gi1/0/10: access 100", self.device.vlans)
        self.assertIn("Gi1/0/16: trunk 1 13", self.device.vlans)
        self.assertIn("Po1: trunk 1", self.device.vlans)

    def test_unresolvable_peer_kept_as_portless_stub(self):
        # Сосед SB-AS-Master-rt ещё не в хранилище — сохраняем связь по имени.
        entries = [e for e in self.device.uplinks if e.startswith("Gi1/0/1 ")]
        self.assertEqual(entries, ["Gi1/0/1 -> SB-AS-Master-rt"])
        self.assertTrue(any("SB-AS-Master-rt" in h for h in self.hints))

    def test_mgm_net1_hint_unresolved_without_catalog(self):
        entries = [e for e in self.device.uplinks if e.startswith("Gi1/0/10")]
        self.assertEqual(entries, ["Gi1/0/10 -> MGM_NET1"])


class RouterImportTest(unittest.TestCase):
    def test_router_detected_by_boot_and_eigrp(self):
        device, _hints = parse_config(ROUTER_CONFIG)
        self.assertEqual(device.kind, "router")

    def test_router_port_channel_and_vlan(self):
        device, _hints = parse_config(ROUTER_CONFIG)
        self.assertIn("Po1", device.ports)
        self.assertIn("Gi0/1/0: access 302", device.vlans)

    def test_peer_resolved_by_fuzzy_shortened_name(self):
        switch = Device(device_id="bfn-as-me-sb-sw", name="BFN-AS-ME-SB-sw")
        device, hints = parse_config(ROUTER_CONFIG, catalog=[switch])
        self.assertEqual(device.uplinks, ["Po1 -> bfn-as-me-sb-sw"])
        self.assertEqual(hints, [])

    def test_reverse_port_picked_up_from_peers_stub(self):
        switch = Device(device_id="bfn-as-me-sb-sw", name="BFN-AS-ME-SB-sw",
                        uplinks=["Gi1/0/1 -> SB-AS-Master-rt"])
        device, _hints = parse_config(ROUTER_CONFIG, catalog=[switch])
        self.assertEqual(device.uplinks, ["Po1 -> bfn-as-me-sb-sw:Gi1/0/1"])


class EdgeCaseTest(unittest.TestCase):
    def test_missing_hostname_is_flagged(self):
        # Без `hostname` модель Device сама подставляет заглушку "device" —
        # importer сюда не вмешивается, а сообщает об этом отдельным hint'ом.
        _device, hints = parse_config(NO_HOSTNAME_CONFIG)
        self.assertTrue(any("hostname" in h for h in hints))

    def test_no_ports_configured_falls_back_to_other(self):
        device, _hints = parse_config(NO_HOSTNAME_CONFIG)
        self.assertEqual(device.ports, [])

    def test_ambiguous_short_name_is_not_guessed(self):
        # Два устройства одинаково подходят под подстроку — не угадываем.
        a = Device(device_id="core-1", name="core-1")
        b = Device(device_id="core-2", name="core-2")
        text = "hostname test\n!\ninterface GigabitEthernet0/1\n description To core\n!\nend\n"
        device, hints = parse_config(text, catalog=[a, b])
        self.assertEqual(device.uplinks, ["Gi0/1 -> core"])
        self.assertTrue(any("core" in h for h in hints))


if __name__ == "__main__":
    unittest.main()

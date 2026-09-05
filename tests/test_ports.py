"""Тесты портов: группы (Gi1/0/1-48) и настройка VLAN на порту."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netcore import Device, ports  # noqa: E402


class GroupTest(unittest.TestCase):
    def test_cisco_group(self):
        self.assertEqual(ports.parse_group("Gi1/0/1-48"), ("Gi", "1/0/", 1, 48))
        self.assertEqual(ports.expand_group("Gi1/0/1-3"), ["Gi1/0/1", "Gi1/0/2", "Gi1/0/3"])

    def test_juniper_group(self):
        self.assertEqual(ports.parse_group("ge-0/0/0-47"), ("ge-", "0/0/", 0, 47))
        self.assertEqual(ports.expand_group("xe-0/0/0-1"), ["xe-0/0/0", "xe-0/0/1"])

    def test_single_port_and_flat_numbering(self):
        self.assertEqual(ports.expand_group("mgmt0"), ["mgmt0"])
        self.assertEqual(ports.expand_group("Eth1-2"), ["Eth1", "Eth2"])

    def test_reversed_range_still_works(self):
        self.assertEqual(ports.expand_group("Gi1/0/3-1"), ["Gi1/0/1", "Gi1/0/2", "Gi1/0/3"])

    def test_garbage_is_not_a_group(self):
        for text in ("", "просто текст", "Gi", "10"):
            self.assertIsNone(ports.parse_group(text))
            self.assertEqual(ports.expand_group(text), [])

    def test_make_group_matches_parser(self):
        spec = ports.make_group("Te", "1/0", 49, 4)
        self.assertEqual(spec, "Te1/0/49-52")
        self.assertEqual(ports.expand_group(spec)[-1], "Te1/0/52")
        self.assertEqual(ports.make_group("Po", "", 1, 1), "Po1")

    def test_expand_keeps_order_without_repeats(self):
        names = ports.expand(["Gi1/0/1-2", "Gi1/0/1-3", "Te1/0/49"])
        self.assertEqual(names, ["Gi1/0/1", "Gi1/0/2", "Gi1/0/3", "Te1/0/49"])

    def test_huge_range_is_capped(self):
        self.assertEqual(len(ports.expand(["Gi1/0/1-999999"])), ports.MAX_PORTS)

    def test_describe_counts_ports_in_russian(self):
        self.assertEqual(ports.describe_group("Gi1/0/1-48"), "Gi1/0/1-48 — 48 портов")
        self.assertEqual(ports.describe_group("Te1/0/49-52"), "Te1/0/49-52 — 4 порта")
        self.assertEqual(ports.describe_group("Po1"), "Po1 — 1 порт")


class PortVlanTest(unittest.TestCase):
    def test_access_and_trunk(self):
        self.assertEqual(ports.parse_vlan("Gi1/0/1: access 10"), ("Gi1/0/1", "access", ["10"]))
        self.assertEqual(ports.parse_vlan("Te1/0/49: trunk 10 20 30"),
                         ("Te1/0/49", "trunk", ["10", "20", "30"]))

    def test_vlan_of_whole_device_stays_as_before(self):
        self.assertEqual(ports.parse_vlan("10"), ("", "", ["10"]))
        self.assertEqual(ports.parse_vlan("100 управление"), ("", "", ["100 управление"]))

    def test_number_with_colon_is_not_a_port(self):
        self.assertEqual(ports.parse_vlan("10: управление"), ("", "", ["10: управление"]))

    def test_mode_may_be_omitted(self):
        self.assertEqual(ports.parse_vlan("Gi1/0/1: 10 20"), ("Gi1/0/1", "", ["10", "20"]))

    def test_format_roundtrip(self):
        for entry in ("Gi1/0/1: access 10", "Te1/0/49: trunk 10 20", "Gi1/0/2: 30"):
            port, mode, vlans = ports.parse_vlan(entry)
            self.assertEqual(ports.format_vlan(port, mode, vlans), entry)

    def test_empty_port_setting_is_dropped(self):
        self.assertEqual(ports.format_vlan("Gi1/0/1", "", []), "")


class DevicePortsTest(unittest.TestCase):
    def test_ports_survive_the_note(self):
        device = Device(name="core-sw-01", kind="switch", mgmt_ip="10.0.0.1",
                        ports=["Gi1/0/1-48", "Te1/0/49-52"],
                        uplinks=["Te1/0/52 -> balkan-sw-01:Te1/0/49"],
                        vlans=["10", "Gi1/0/1: access 10"])
        again = Device.from_markdown(device.to_markdown())
        self.assertEqual(again.ports, ["Gi1/0/1-48", "Te1/0/49-52"])
        self.assertEqual(again.vlans, ["10", "Gi1/0/1: access 10"])
        self.assertEqual(len(again.port_names()), 52)
        self.assertEqual(again.configured_ports(), ["Te1/0/52", "Gi1/0/1"])
        self.assertEqual(again.validate(), [])

    def test_port_outside_the_list_is_a_problem(self):
        device = Device(name="sw", kind="switch", mgmt_ip="10.0.0.1", ports=["Gi1/0/1-4"],
                        uplinks=["Gi1/0/9 -> peer:Gi1/0/1"])
        self.assertIn("настроены порты, которых нет в списке портов: Gi1/0/9",
                      device.validate())

    def test_without_port_list_nothing_is_checked(self):
        device = Device(name="sw", kind="switch", mgmt_ip="10.0.0.1",
                        uplinks=["Gi1/0/9 -> peer:Gi1/0/1"])
        self.assertEqual(device.validate(), [])

    def test_ports_are_searchable(self):
        device = Device(name="sw", kind="switch", ports=["Te1/0/49-52"])
        self.assertIn("te1/0/49-52", device.searchable_text())


if __name__ == "__main__":
    unittest.main()

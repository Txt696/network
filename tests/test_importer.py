"""Тесты разбора конфигурации Cisco в устройство (netcore/importer.py)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netcore import Vault  # noqa: E402
from netcore.importer import (  # noqa: E402
    collect_files, format_report, import_files, merge_into, parse_config)
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
        entries = [e for e in self.device.uplinks if e.startswith("Po1 ")]
        self.assertEqual(entries, ["Po1 -> SB-AS-Master-rt"])
        self.assertTrue(any("SB-AS-Master-rt" in h for h in self.hints))

    def test_bundled_member_does_not_double_the_link(self):
        # Gi1/0/1 — член Po1 (channel-group 1), и связь уже записана на Po1:
        # вторая линия к тому же соседу на карте не нужна.
        self.assertEqual([e for e in self.device.uplinks if e.startswith("Gi1/0/1 ")], [])
        self.assertTrue(any("агрегат" in h for h in self.hints), self.hints)

    def test_description_with_parenthetical_still_names_the_peer(self):
        device, _hints = parse_config(
            "hostname sw1\n!\ninterface GigabitEthernet1/0/1\n"
            " description To BFN-AS-ME-tkm-CB-sw (new CBS350-24)\n"
            " switchport mode trunk\n!\nend\n")
        self.assertEqual(device.uplinks, ["Gi1/0/1 -> BFN-AS-ME-tkm-CB-sw"])

    def test_for_is_read_as_an_uplink_just_like_to(self):
        device, _hints = parse_config(
            "hostname sw1\n!\ninterface Port-channel1\n"
            " description For SB-AS-Master-rt\n!\nend\n")
        self.assertEqual(device.uplinks, ["Po1 -> SB-AS-Master-rt"])

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


class FolderImportTest(unittest.TestCase):
    """Пакетный импорт: папка конфигов -> заполненное хранилище."""

    PASSWORD = "test-master-password"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.configs = root / "configs"
        (self.configs / "backup").mkdir(parents=True)
        (self.configs / "switch.txt").write_text(SWITCH_CONFIG, encoding="utf-8")
        (self.configs / "backup" / "router.cfg").write_text(ROUTER_CONFIG, encoding="utf-8")
        (self.configs / "readme.md").write_text("не конфиг", encoding="utf-8")
        self.vault = Vault.create(root / "vault", self.PASSWORD)

    def tearDown(self):
        self.tmp.cleanup()

    def test_collect_files_takes_configs_recursively(self):
        names = [path.name for path in collect_files([self.configs])]
        self.assertEqual(names, ["router.cfg", "switch.txt"])

    def test_collect_files_can_stay_flat(self):
        names = [path.name for path in collect_files([self.configs], recursive=False)]
        self.assertEqual(names, ["switch.txt"])

    def test_collect_files_drops_duplicate_paths(self):
        both = collect_files([self.configs, self.configs / "switch.txt"])
        self.assertEqual([path.name for path in both], ["router.cfg", "switch.txt"])

    def test_folder_import_writes_every_device(self):
        report = import_files(self.vault, [self.configs])
        self.assertEqual([record["action"] for record in report], ["created", "created"])
        self.assertEqual(sorted(device.id for device in self.vault.devices()),
                         ["bfn-as-me-sb-sw", "sb-as-master-rt"])

    def test_link_inside_batch_is_filled_on_both_sides(self):
        # Свитч ссылается на роутер, роутер — на свитч, и оба конфига лежат
        # в одной папке: порт соседа должен проставиться с обеих сторон,
        # хотя в хранилище до импорта не было ни того, ни другого.
        import_files(self.vault, [self.configs])
        # Линк описан на агрегатах с обеих сторон (Po1 «For SB-AS-Master-rt»
        # у свитча, Po1 «To BFN-AS-ME» у роутера) — он и становится связью.
        self.assertIn("Po1 -> sb-as-master-rt:Po1",
                      self.vault.get("bfn-as-me-sb-sw").uplinks)
        self.assertIn("Po1 -> bfn-as-me-sb-sw:Po1",
                      self.vault.get("sb-as-master-rt").uplinks)
        # На карте эта пара портов — одна связь, а не две встречные.
        touching = [link for link in self.vault.links()
                    if {link["source"], link["target"]}
                    == {"bfn-as-me-sb-sw", "sb-as-master-rt"}]
        self.assertEqual(len(touching), 1, touching)

    def test_batch_result_does_not_depend_on_file_order(self):
        # Обратный порядок обхода даёт то же самое: разбор идёт в два прохода.
        import_files(self.vault, [self.configs / "backup" / "router.cfg",
                                 self.configs / "switch.txt"])
        router = self.vault.get("sb-as-master-rt")
        self.assertIn("Po1 -> bfn-as-me-sb-sw:Po1", router.uplinks)

    def test_reimport_updates_instead_of_duplicating(self):
        import_files(self.vault, [self.configs])
        device = self.vault.get("bfn-as-me-sb-sw")
        device.site, device.tags, device.rack = "Балкан", ["core"], "R12"
        self.vault.save(device)

        report = import_files(self.vault, [self.configs])
        self.assertEqual([record["action"] for record in report], ["updated", "updated"])
        self.assertEqual(len(self.vault.devices()), 2)
        again = self.vault.get("bfn-as-me-sb-sw")
        self.assertEqual((again.site, again.tags, again.rack), ("Балкан", ["core"], "R12"))
        self.assertEqual(again.mgmt_ip, "10.240.40.4")

    def test_site_is_set_on_new_devices_only(self):
        import_files(self.vault, [self.configs / "switch.txt"], site="Ашхабад")
        self.assertEqual(self.vault.get("bfn-as-me-sb-sw").site, "Ашхабад")
        import_files(self.vault, [self.configs], site="Балкан")
        self.assertEqual(self.vault.get("bfn-as-me-sb-sw").site, "Ашхабад")
        self.assertEqual(self.vault.get("sb-as-master-rt").site, "Балкан")

    def test_manual_uplink_on_untouched_port_survives_reimport(self):
        # Конфиг знает только о портах с описанием соседа; про Gi1/0/14
        # он молчит — значит, ручную настройку этого порта импорт не трогает.
        import_files(self.vault, [self.configs / "switch.txt"])
        device = self.vault.get("bfn-as-me-sb-sw")
        device.uplinks.append("Gi1/0/14 -> sb-as-master-rt:Gi0/0/3")
        self.vault.save(device)

        import_files(self.vault, [self.configs / "switch.txt"])
        self.assertIn("Gi1/0/14 -> sb-as-master-rt:Gi0/0/3",
                      self.vault.get("bfn-as-me-sb-sw").uplinks)

    def test_config_wins_over_stale_uplink_of_the_same_port(self):
        import_files(self.vault, [self.configs / "switch.txt"])
        device = self.vault.get("bfn-as-me-sb-sw")
        device.uplinks = ["Gi1/0/16 -> нет-такого:Te0/1"]
        self.vault.save(device)

        import_files(self.vault, [self.configs / "switch.txt"])
        ports_used = [entry for entry in self.vault.get("bfn-as-me-sb-sw").uplinks
                      if entry.startswith("Gi1/0/16 ")]
        self.assertEqual(len(ports_used), 1, ports_used)
        self.assertNotIn("нет-такого", ports_used[0])

    def test_file_without_hostname_is_reported_not_fatal(self):
        (self.configs / "broken.txt").write_text(NO_HOSTNAME_CONFIG, encoding="utf-8")
        report = import_files(self.vault, [self.configs])
        broken = [record for record in report if record["path"].endswith("broken.txt")][0]
        self.assertEqual(broken["action"], "failed")
        self.assertIn("hostname", broken["error"])
        self.assertEqual(len(self.vault.devices()), 2)
        self.assertIn("Пропущено", format_report(report))

    def test_missing_path_is_simply_empty(self):
        self.assertEqual(import_files(self.vault, [self.configs / "нет-такой-папки"]), [])

    def test_peer_port_is_not_handed_to_two_ports_at_once(self):
        # Два порта свитча описаны одинаково («To SB-AS-Master-rt»), но Po1
        # роутера физически один: он достаётся первому, второй остаётся
        # связью без порта, а не вторым владельцем того же порта.
        second = SWITCH_CONFIG.replace(
            "interface GigabitEthernet1/0/16",
            "interface GigabitEthernet2/0/1\n"
            " description To SB-AS-Master-rt Cross-2\n"
            " switchport mode trunk\n"
            "!\n"
            "interface GigabitEthernet1/0/16")
        (self.configs / "switch.txt").write_text(second, encoding="utf-8")
        import_files(self.vault, [self.configs])
        uplinks = self.vault.get("bfn-as-me-sb-sw").uplinks
        self.assertIn("Po1 -> sb-as-master-rt:Po1", uplinks)
        self.assertIn("Gi2/0/1 -> sb-as-master-rt", uplinks)

    def test_similar_names_are_split_by_the_answering_side(self):
        # «To BFN-AS-ME» подходит и к BFN-AS-ME-SB-sw, и к BFN-AS-ME-SB-tkm-sw.
        # Решает встречная сторона: нас упоминает только первый.
        tkm = Device(name="BFN-AS-ME-SB-tkm-sw", kind="switch",
                     uplinks=["Gi1/0/1 -> BFN-AS-ME-tkm-CB-sw"])
        me = Device(name="BFN-AS-ME-SB-sw", kind="switch",
                    uplinks=["Po1 -> SB-AS-Master-rt"])
        device, _hints = parse_config(ROUTER_CONFIG, catalog=[tkm, me])
        # Заодно подхватился и порт соседа из его встречной записи.
        self.assertIn("Po1 -> bfn-as-me-sb-sw:Po1", device.uplinks)

    def test_similar_names_without_an_answer_stay_unresolved(self):
        # Никто из похожих на нас не ссылается — гадать не надо, связь
        # сохраняется по имени и достраивается позже.
        twins = [Device(name="BFN-AS-ME-SB-sw", kind="switch"),
                 Device(name="BFN-AS-ME-SB-tkm-sw", kind="switch")]
        device, _hints = parse_config(ROUTER_CONFIG, catalog=twins)
        self.assertIn("Po1 -> BFN-AS-ME", device.uplinks)

    def test_merge_keeps_identity_of_existing_device(self):
        parsed, _hints = parse_config(SWITCH_CONFIG)
        existing = Device(device_id="old-id", name="BFN-AS-ME-SB-sw", kind="other",
                          site="Балкан", secret="secret-123")
        merged = merge_into(existing, parsed)
        self.assertEqual((merged.id, merged.site, merged.secret),
                         ("old-id", "Балкан", "secret-123"))
        self.assertEqual(merged.kind, "switch")

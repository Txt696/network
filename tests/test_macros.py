"""Тесты вспомогательных модулей NetMaster: разбор вывода и наборы команд."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netcore import Vault  # noqa: E402
from netmaster.core import ansi, macros  # noqa: E402

PASSWORD = "test-master-password"


class AnsiTest(unittest.TestCase):
    def test_plain_text_survives(self):
        self.assertEqual(ansi.clean("show version\nCisco IOS"), "show version\nCisco IOS")

    def test_colors_are_stripped(self):
        self.assertEqual(ansi.clean("\x1b[31mERROR\x1b[0m: down"), "ERROR: down")

    def test_crlf_becomes_lf(self):
        self.assertEqual(ansi.clean("line1\r\nline2\r\n"), "line1\nline2\n")

    def test_backspace_erases_previous_character(self):
        done, current = ansi.apply_edits("", "abc\b\b")
        self.assertEqual(done, [])
        self.assertEqual(current, "a")

    def test_carriage_return_restarts_line(self):
        _done, current = ansi.apply_edits("", "abc\rX")
        self.assertEqual(current, "X")

    def test_newline_closes_line(self):
        done, current = ansi.apply_edits("pre", "fix\ntail")
        self.assertEqual(done, ["prefix\n"])
        self.assertEqual(current, "tail")

    def test_ctrl_code(self):
        self.assertEqual(ansi.ctrl_code("c"), "\x03")
        self.assertEqual(ansi.ctrl_code("C"), "\x03")
        self.assertEqual(ansi.ctrl_code("1"), "")


class KeyDataTest(unittest.TestCase):
    """Что уходит на устройство по нажатой клавише."""

    def setUp(self):
        self.alt = ansi.ALT_MASK
        self.numlock = 0x8  # на Windows этим битом помечается NumLock

    def windows(self):
        """Подменить маску Alt на windows-вариант."""
        ansi.ALT_MASK = 0x20000
        self.addCleanup(setattr, ansi, "ALT_MASK", self.alt)

    def test_plain_character(self):
        self.assertEqual(ansi.key_data("a", "a", 0), "a")

    def test_special_keys(self):
        self.assertEqual(ansi.key_data("Return", "\r", 0), "\r")
        self.assertEqual(ansi.key_data("Up", "", 0), "\x1b[A")
        self.assertEqual(ansi.key_data("BackSpace", "\x08", 0), "\x7f")

    def test_modifier_alone_sends_nothing(self):
        self.assertEqual(ansi.key_data("Shift_L", "", 0), "")

    def test_control_letter(self):
        self.assertEqual(ansi.key_data("c", "\x03", ansi.CONTROL_MASK), "\x03")

    def test_alt_is_left_to_the_system(self):
        self.assertIsNone(ansi.key_data("f", "f", ansi.ALT_MASK))

    def test_numlock_does_not_block_input_on_windows(self):
        """С включённым NumLock на Windows ввод должен работать."""
        self.windows()
        self.assertEqual(ansi.key_data("a", "a", self.numlock), "a")
        self.assertEqual(ansi.key_data("Return", "\r", self.numlock), "\r")

    def test_windows_alt_is_still_recognised(self):
        self.windows()
        self.assertIsNone(ansi.key_data("f", "f", 0x20000))


class MacrosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Vault.create(Path(self.tmp.name) / "vault", PASSWORD)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_without_vault(self):
        items = macros.load()
        self.assertTrue(items)
        self.assertTrue(all(item["name"] and item["commands"] for item in items))

    def test_fresh_vault_gets_defaults(self):
        self.assertEqual(macros.load(self.vault), macros.load())

    def test_save_then_load_roundtrip(self):
        mine = [{"name": "Мои порты", "vendor": "cisco",
                 "commands": ["show interfaces status", "show power inline"]}]
        macros.save(self.vault, mine)
        self.assertEqual(macros.load(self.vault), mine)

    def test_broken_entries_are_dropped(self):
        macros.save(self.vault, [{"name": "", "commands": ["x"]},
                                 {"name": "Пусто", "commands": []},
                                 {"name": "Ок", "commands": [" show version "]}])
        items = macros.load(self.vault)
        self.assertEqual([item["name"] for item in items], ["Ок"])
        self.assertEqual(items[0]["commands"], ["show version"])

    def test_for_vendor_filters_by_vendor(self):
        items = macros.load()
        names = [item["commands"][0] for item in macros.for_vendor(items, "Cisco IOS")]
        self.assertIn("show version", names)
        self.assertNotIn("/export", names)

    def test_for_vendor_keeps_common_macros(self):
        common = [item for item in macros.for_vendor(macros.load(), "cisco")
                  if not item["vendor"]]
        self.assertTrue(common, "общие макросы должны показываться любому устройству")

    def test_unknown_vendor_gets_only_common(self):
        items = macros.for_vendor(macros.load(), "неизвестный")
        self.assertTrue(all(not item["vendor"] for item in items))


if __name__ == "__main__":
    unittest.main()

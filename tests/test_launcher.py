"""Тесты запуска соседнего приложения — из исходников и из собранного файла."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netcore import launcher  # noqa: E402


class SourceModeTest(unittest.TestCase):
    """Запуск из исходников: тем же Python, что и текущее приложение."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "netmaster").mkdir()
        (self.root / "netmaster" / "main.py").write_text("", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_runs_script_with_python(self):
        command, work_dir = launcher.command_for(
            "NetMaster", "netmaster/main.py",
            frozen=False, executable="/usr/bin/python3", source_root=self.root)
        self.assertEqual(command[0], "/usr/bin/python3")
        self.assertTrue(command[1].endswith(os.path.join("netmaster", "main.py")))
        self.assertEqual(work_dir, str(self.root))

    def test_missing_script(self):
        self.assertIsNone(launcher.command_for(
            "NetVault", "netvault/main.py",
            frozen=False, executable="/usr/bin/python3", source_root=self.root))


class FrozenModeTest(unittest.TestCase):
    """Собранная программа: сосед ищется рядом, а не в исходниках."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.me = self.folder / launcher.exe_name("NetVault")
        self.me.write_text("", encoding="utf-8")
        self.neighbour = self.folder / launcher.exe_name("NetMaster")

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_neighbour_next_to_itself(self):
        self.neighbour.write_text("", encoding="utf-8")
        command, work_dir = launcher.command_for(
            "NetMaster", "netmaster/main.py", frozen=True, executable=str(self.me))
        self.assertEqual(command, [str(self.neighbour)])
        self.assertEqual(work_dir, str(self.folder))

    def test_no_neighbour(self):
        self.assertIsNone(launcher.command_for(
            "NetMaster", "netmaster/main.py", frozen=True, executable=str(self.me)))

    def test_source_tree_is_not_used_when_frozen(self):
        """Даже если рядом лежат исходники, собранная программа ищет exe."""
        (self.folder / "netmaster").mkdir()
        (self.folder / "netmaster" / "main.py").write_text("", encoding="utf-8")
        self.assertIsNone(launcher.command_for(
            "NetMaster", "netmaster/main.py", frozen=True,
            executable=str(self.me), source_root=self.folder))

    def test_message_explains_same_folder_rule(self):
        message = launcher.not_found_message("NetMaster", frozen=True,
                                             executable=str(self.me))
        self.assertIn("одну папку", message)
        self.assertIn(str(self.folder), message)


if __name__ == "__main__":
    unittest.main()

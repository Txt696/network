"""Тесты файловой части: пути на устройстве, размеры, порядок списка."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netmaster.core import sftp  # noqa: E402


class PathTest(unittest.TestCase):
    def test_join_uses_forward_slashes(self):
        self.assertEqual(sftp.join("/home/admin", "config.txt"), "/home/admin/config.txt")

    def test_join_from_root(self):
        self.assertEqual(sftp.join("/", "flash"), "/flash")

    def test_join_normalises_dots(self):
        self.assertEqual(sftp.join("/home/admin", ".."), "/home")

    def test_parent(self):
        self.assertEqual(sftp.parent("/home/admin/logs"), "/home/admin")

    def test_parent_of_root_is_root(self):
        self.assertEqual(sftp.parent("/"), "/")
        self.assertEqual(sftp.parent(""), "/")


class SizeTest(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(sftp.human_size(512), "512 Б")

    def test_kilobytes(self):
        self.assertEqual(sftp.human_size(2048), "2.0 КБ")

    def test_megabytes(self):
        self.assertEqual(sftp.human_size(5 * 1024 * 1024), "5.0 МБ")

    def test_broken_value(self):
        self.assertEqual(sftp.human_size(None), "")


class EntryTest(unittest.TestCase):
    def test_directories_come_first(self):
        entries = [sftp.Entry("zebra.cfg"), sftp.Entry("logs", is_dir=True),
                   sftp.Entry("alpha.cfg"), sftp.Entry("Archive", is_dir=True)]
        names = [e.name for e in sftp.sort_entries(entries)]
        self.assertEqual(names, ["Archive", "logs", "alpha.cfg", "zebra.cfg"])

    def test_size_text_for_directory(self):
        self.assertEqual(sftp.Entry("logs", is_dir=True).size_text, "<папка>")

    def test_size_text_for_file(self):
        self.assertEqual(sftp.Entry("run.cfg", size=1024).size_text, "1.0 КБ")


if __name__ == "__main__":
    unittest.main()

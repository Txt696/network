"""Тесты ядра: разбор фронтматтера, переименование устройств, доступы."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netcore import Device, Vault  # noqa: E402
from netcore import frontmatter  # noqa: E402

PASSWORD = "test-master-password"


class FrontmatterTest(unittest.TestCase):
    def roundtrip(self, meta):
        text = frontmatter.dump(meta, "тело заметки")
        again, body = frontmatter.parse(text)
        self.assertEqual(body.strip(), "тело заметки")
        return again

    def test_quotes_and_backslashes_survive(self):
        meta = {"vendor": 'ACME "R1"', "note": r"C:\configs\core", "name": "core-sw-01"}
        self.assertEqual(self.roundtrip(meta), meta)

    def test_quoted_value_is_unescaped_on_read(self):
        meta, _ = frontmatter.parse('---\nvendor: "ACME \\"R1\\""\n---\n')
        self.assertEqual(meta["vendor"], 'ACME "R1"')

    def test_single_quotes(self):
        meta, _ = frontmatter.parse("---\nrole: 'it''s core'\n---\n")
        self.assertEqual(meta["role"], "it's core")

    def test_inline_list_keeps_commas_inside_quotes(self):
        meta, _ = frontmatter.parse('---\ntags: [core, "dc1, rack 3"]\n---\n')
        self.assertEqual(meta["tags"], ["core", "dc1, rack 3"])

    def test_device_note_roundtrip(self):
        device = Device(name="core-sw-01", kind="switch", mgmt_ip="10.0.0.1",
                        vendor='ACME "R1"', tags="core,dc1")
        again = Device.from_markdown(device.to_markdown(), device_id=device.id)
        self.assertEqual(again.vendor, 'ACME "R1"')
        self.assertEqual(again.tags, ["core", "dc1"])


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Vault.create(Path(self.tmp.name) / "vault", PASSWORD)
        self.vault.unlock(PASSWORD)
        self.device = Device(name="core-sw-01", kind="switch", mgmt_ip="10.0.0.1")
        self.vault.save(self.device)
        self.vault.secrets.put(self.device.secret_ref, username="admin", password="Pa55")

    def tearDown(self):
        self.tmp.cleanup()

    def rename_to(self, new_id):
        old_id = self.device.id
        self.device.id = new_id
        self.vault.save(self.device, rename_from=old_id)
        return old_id

    def test_rename_moves_note_and_secret(self):
        old_id = self.rename_to("core-sw-02")
        self.assertFalse(self.vault.device_path(old_id).exists())
        self.assertIsNotNone(self.vault.get("core-sw-02"))
        found = self.vault.secrets.get(self.vault.get("core-sw-02").secret_ref)
        self.assertEqual(found["username"], "admin")

    def test_rename_while_locked_keeps_credentials_reachable(self):
        self.vault.lock()
        self.rename_to("core-sw-02")
        self.vault.unlock(PASSWORD)
        device = self.vault.get("core-sw-02")
        self.assertIsNotNone(self.vault.secrets.get(device.secret_ref),
                             "доступы должны остаться доступными после переименования")

    def test_failed_write_keeps_old_note(self):
        old_id = self.device.id
        self.device.id = "core-sw-02"
        broken = Device(name="broken")
        broken.to_markdown = lambda: (_ for _ in ()).throw(OSError("диск переполнен"))
        broken.id, broken.secret = "core-sw-02", ""
        with self.assertRaises(OSError):
            self.vault.save(broken, rename_from=old_id)
        self.assertTrue(self.vault.device_path(old_id).exists(),
                        "старая заметка не должна пропадать при сбое записи")

    def test_secrets_are_not_in_plain_text(self):
        blob = (self.vault.path / "secrets.enc").read_bytes()
        self.assertNotIn(b"Pa55", blob)
        self.assertNotIn(b"admin", blob)

    def test_locked_vault_hides_secrets(self):
        self.vault.lock()
        with self.assertRaises(Exception):
            self.vault.secrets.get(self.device.secret_ref)


if __name__ == "__main__":
    unittest.main()

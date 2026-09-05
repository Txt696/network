"""
Vault — хранилище инвентаря: папка с Markdown-заметками + файл секретов.

Структура на диске:
    <vault>/
        devices/           один .md на устройство (открывается в Obsidian)
        notes/             свободные заметки и собранные конфиги
        templates/         шаблоны заметок
        .netvault/
            config.json    настройки хранилища
            history.jsonl  журнал изменений
        secrets.enc        зашифрованные доступы
        README.md
"""

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .links import describe as describe_link, parse as parse_link
from .models import Device, now_stamp, slugify
from .secretstore import SecretStore

DEVICES_DIR = "devices"
NOTES_DIR = "notes"
TEMPLATES_DIR = "templates"
META_DIR = ".netvault"
SECRETS_FILE = "secrets.enc"
CONFIG_FILE = "config.json"
HISTORY_FILE = "history.jsonl"

DEFAULT_CONFIG = {
    "format": "netvault",
    "version": 1,
    "autolock_minutes": 10,
    "clipboard_clear_seconds": 30,
}

README = """# NetVault

Хранилище данных о серверах, свитчах и роутерах.

- `devices/` — по одной заметке на устройство (YAML-фронтматтер + свободный текст).
  Папку можно открыть как vault в Obsidian.
- `notes/` — произвольные заметки и собранные с устройств конфиги.
- `secrets.enc` — логины и пароли, зашифрованы мастер-паролем (AES-256-GCM).
  Этот файл бесполезен без мастер-пароля, но и восстановить его без пароля нельзя.

Открывать и редактировать: приложение NetVault. Управлять устройствами: NetMaster.
"""

DEVICE_TEMPLATE = """---
name: Новое устройство
kind: switch
mgmt_ip: 10.0.0.1
vendor: Cisco
model: ""
site: ""
rack: ""
role: ""
protocol: ssh
port: 22
secret: ""
status: active
tags: []
ports: []
uplinks: []
vlans: []
---

## Назначение

## Что настроено

## История изменений
"""


class VaultError(Exception):
    pass


def _atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Vault:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.secrets = SecretStore(self.path / SECRETS_FILE)
        self._config = None

    # ------------------------------------------------------------ создание
    @classmethod
    def create(cls, path, master_password):
        """Создать новое хранилище с мастер-паролем."""
        vault = cls(path)
        if vault.is_vault:
            raise VaultError("Хранилище уже существует: %s" % vault.path)
        for sub in (DEVICES_DIR, NOTES_DIR, TEMPLATES_DIR, META_DIR):
            (vault.path / sub).mkdir(parents=True, exist_ok=True)
        (vault.path / "README.md").write_text(README, encoding="utf-8")
        (vault.path / TEMPLATES_DIR / "device.md").write_text(DEVICE_TEMPLATE, encoding="utf-8")
        vault._write_config(dict(DEFAULT_CONFIG, created=now_stamp()))
        SecretStore.create(vault.path / SECRETS_FILE, master_password)
        vault.secrets.unlock(master_password)
        vault.log("vault", "created", {"path": str(vault.path)})
        return vault

    @property
    def is_vault(self):
        return (self.path / SECRETS_FILE).exists() and (self.path / DEVICES_DIR).is_dir()

    def require_vault(self):
        if not self.is_vault:
            raise VaultError("По пути %s нет хранилища NetVault" % self.path)

    # ------------------------------------------------------------ настройки
    @property
    def config(self):
        if self._config is None:
            cfg_path = self.path / META_DIR / CONFIG_FILE
            try:
                self._config = dict(DEFAULT_CONFIG, **json.loads(cfg_path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                self._config = dict(DEFAULT_CONFIG)
        return self._config

    def set_config(self, **values):
        cfg = dict(self.config, **values)
        self._write_config(cfg)
        self._config = cfg
        return cfg

    def _write_config(self, cfg):
        (self.path / META_DIR).mkdir(parents=True, exist_ok=True)
        _atomic_write_text(self.path / META_DIR / CONFIG_FILE, json.dumps(cfg, ensure_ascii=False, indent=2))

    # ------------------------------------------------------------ блокировка
    def unlock(self, master_password):
        self.require_vault()
        self.secrets.unlock(master_password)
        return self

    def lock(self):
        self.secrets.lock()

    @property
    def is_locked(self):
        return self.secrets.is_locked

    def autolock_seconds(self):
        return int(self.config.get("autolock_minutes", 10)) * 60

    # ------------------------------------------------------------ устройства
    def device_path(self, device_id):
        return self.path / DEVICES_DIR / ("%s.md" % device_id)

    def devices(self):
        """Все устройства, отсортированные по имени. Заметки читаются с диска."""
        self.require_vault()
        result = []
        for md in sorted((self.path / DEVICES_DIR).glob("*.md")):
            try:
                result.append(Device.from_markdown(md.read_text(encoding="utf-8"), device_id=md.stem))
            except (OSError, ValueError):
                continue
        result.sort(key=lambda d: (d.site.lower(), d.name.lower()))
        return result

    def get(self, device_id):
        path = self.device_path(device_id)
        if not path.exists():
            return None
        return Device.from_markdown(path.read_text(encoding="utf-8"), device_id=device_id)

    def save(self, device, rename_from=None):
        """Записать устройство на диск. Возвращает путь к файлу."""
        self.require_vault()
        if not device.id:
            device.id = slugify(device.name)
        device.updated = now_stamp()
        target = self.device_path(device.id)
        renaming = bool(rename_from) and rename_from != device.id
        if renaming:
            old = self.device_path(rename_from)
            if target.exists():
                raise VaultError("Устройство с id '%s' уже есть" % device.id)
            if self.secrets.is_locked:
                # Пароли перенести нельзя, пока хранилище закрыто, — поэтому
                # закрепляем ссылку на старый id прямо в заметке, чтобы запись
                # доступов не осиротела.
                if not device.secret:
                    device.secret = rename_from
            elif self.secrets.get(rename_from):
                self.secrets.rename(rename_from, device.secret_ref)
        # Сначала новый файл, и только потом удаление старого: если запись
        # сорвётся, устройство останется на диске под прежним именем.
        _atomic_write_text(target, device.to_markdown())
        if renaming and old.exists():
            old.unlink()
        self.log(device.id, "saved", {"name": device.name, "ip": device.mgmt_ip})
        return target

    def delete(self, device_id, delete_secret=True):
        path = self.device_path(device_id)
        existed = path.exists()
        if existed:
            device = self.get(device_id)
            path.unlink()
            if delete_secret and not self.secrets.is_locked:
                self.secrets.delete(device.secret_ref)
            self.log(device_id, "deleted", {})
        return existed

    def unique_id(self, base_name):
        """Свободный id на основе имени (name, name-2, name-3...)."""
        base = slugify(base_name)
        candidate, counter = base, 2
        while self.device_path(candidate).exists():
            candidate = "%s-%d" % (base, counter)
            counter += 1
        return candidate

    # --------------------------------------------------------------- поиск
    def search(self, query="", kind=None, site=None, tag=None, status=None, devices=None):
        """Поиск по всем полям и тексту заметок.

        В запросе можно использовать фильтры вида `kind:switch site:dc1`,
        остальные слова ищутся как подстроки (все слова должны найтись).
        """
        items = self.devices() if devices is None else list(devices)
        words = []
        for token in (query or "").split():
            if ":" in token:
                key, _, value = token.partition(":")
                key, value = key.lower(), value.lower()
                if key in ("kind", "type", "тип"):
                    kind = value
                    continue
                if key in ("site", "площадка"):
                    site = value
                    continue
                if key in ("tag", "тег"):
                    tag = value
                    continue
                if key == "status":
                    status = value
                    continue
            words.append(token.lower())

        def matches(device):
            if kind and device.kind.lower() != kind.lower():
                return False
            if site and site.lower() not in device.site.lower():
                return False
            if status and device.status.lower() != status.lower():
                return False
            if tag and tag.lower() not in [t.lower() for t in device.tags]:
                return False
            if words:
                haystack = device.searchable_text()
                return all(word in haystack for word in words)
            return True

        return [d for d in items if matches(d)]

    # ------------------------------------------------------------ топология
    def topology(self, devices=None):
        """Рёбра графа: (устройство, аплинк, найден_ли_аплинк_в_хранилище)."""
        return [(link["source"], link["target"], link["found"])
                for link in self.links(devices)]

    def links(self, devices=None):
        """Связи с портами — то, что рисует карта сети.

        Каждая связь: откуда, куда, свой порт, порт соседа, подпись
        и найден ли сосед в хранилище.
        """
        items = self.devices() if devices is None else list(devices)
        known = {d.id for d in items}
        by_name = {d.name.lower(): d.id for d in items}
        result = []
        for device in items:
            for entry in device.uplinks:
                local_port, peer, peer_port = parse_link(entry)
                if not peer:
                    continue
                target = peer if peer in known else by_name.get(peer.lower())
                result.append({
                    "source": device.id,
                    "target": target or peer,
                    "found": target is not None,
                    "local_port": local_port,
                    "peer_port": peer_port,
                    "label": describe_link(local_port, peer_port),
                })
        return result

    def backlinks(self, device_id, devices=None):
        """Кто указывает это устройство своим аплинком."""
        items = self.devices() if devices is None else list(devices)
        device = self.get(device_id)
        names = {device_id.lower()}
        if device:
            names.add(device.name.lower())
        return [d.id for d in items
                if any(parse_link(l)[1].lower() in names for l in d.uplinks)]

    # --------------------------------------------------------------- заметки
    def add_note(self, device_id, title, text, subdir="collected"):
        """Сохранить заметку (например, вывод команды) в notes/<subdir>/<device>/."""
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
        safe_title = re.sub(r"[^\w.-]+", "-", title).strip("-") or "note"
        path = self.path / NOTES_DIR / subdir / device_id / ("%s_%s.md" % (stamp, safe_title))
        meta_lines = [
            "---",
            "device: %s" % device_id,
            "title: %s" % title,
            "collected: %s" % now_stamp(),
            "---",
            "",
        ]
        _atomic_write_text(path, "\n".join(meta_lines) + text.rstrip() + "\n")
        return path

    def device_notes(self, device_id, subdir="collected"):
        folder = self.path / NOTES_DIR / subdir / device_id
        return sorted(folder.glob("*.md"), reverse=True) if folder.is_dir() else []

    # --------------------------------------------------------------- журнал
    def log(self, subject, action, details=None):
        """Дописать строку в журнал изменений (без секретов)."""
        record = {
            "ts": now_stamp(),
            "subject": subject,
            "action": action,
            "details": details or {},
        }
        try:
            path = self.path / META_DIR / HISTORY_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass  # журнал не должен ломать основную работу
        return record

    def history(self, limit=100):
        path = self.path / META_DIR / HISTORY_FILE
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return list(reversed(out))

    # ---------------------------------------------------------------- сводка
    def stats(self):
        devices = self.devices()
        by_kind, by_site = {}, {}
        for device in devices:
            by_kind[device.kind] = by_kind.get(device.kind, 0) + 1
            by_site[device.site or "—"] = by_site.get(device.site or "—", 0) + 1
        return {
            "total": len(devices),
            "by_kind": dict(sorted(by_kind.items())),
            "by_site": dict(sorted(by_site.items())),
            "without_ip": [d.id for d in devices if not d.target],
            "problems": {d.id: p for d in devices for p in [d.validate()] if p},
        }

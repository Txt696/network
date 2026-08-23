#!/usr/bin/env python3
"""
Консольный интерфейс NetVault — то же хранилище, что и в графическом
приложении. Удобен на сервере без GUI и для скриптов.

Примеры:
    python netvault/cli.py init
    python netvault/cli.py add --name "Core SW 01" --kind switch --ip 10.0.0.1 --site DC1
    python netvault/cli.py list --kind switch
    python netvault/cli.py search "core cisco"
    python netvault/cli.py show core-sw-01 --secret
    python netvault/cli.py set-secret core-sw-01 --username admin
    python netvault/cli.py export --format csv > inventory.csv

Мастер-пароль спрашивается интерактивно; для скриптов можно передать его
через переменную окружения NETVAULT_PASSWORD.
"""

import argparse
import csv
import getpass
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netcore import CryptoError, Device, KINDS, Vault, VaultError  # noqa: E402
from netcore.secretstore import FIELDS as SECRET_FIELDS  # noqa: E402
from netvault import appconfig  # noqa: E402


def ask_password(prompt="Мастер-пароль: "):
    env = os.environ.get(appconfig.ENV_PASSWORD)
    if env:
        return env
    return getpass.getpass(prompt)


def open_vault(args, unlock=True):
    vault = Vault(appconfig.resolve_vault_path(args.vault))
    if not vault.is_vault:
        raise SystemExit(
            "Хранилища нет: %s\nСоздайте его: python netvault/cli.py init --vault <путь>" % vault.path
        )
    if unlock:
        vault.unlock(ask_password())
    appconfig.remember_vault(vault.path)
    return vault


# ------------------------------------------------------------------ команды
def cmd_init(args):
    path = Path(args.vault).expanduser() if args.vault else appconfig.default_vault_path()
    if Vault(path).is_vault:
        raise SystemExit("Хранилище уже существует: %s" % path)
    password = ask_password("Придумайте мастер-пароль: ")
    if not os.environ.get(appconfig.ENV_PASSWORD):
        if password != getpass.getpass("Повторите пароль: "):
            raise SystemExit("Пароли не совпадают")
    if len(password) < 8:
        print("Внимание: пароль короче 8 символов — это слабая защита.", file=sys.stderr)
    vault = Vault.create(path, password)
    appconfig.remember_vault(vault.path)
    print("Хранилище создано: %s" % vault.path)
    print("Пароль восстановить нельзя — сохраните его в надёжном месте.")


def cmd_list(args):
    vault = open_vault(args, unlock=False)
    devices = vault.search("", kind=args.kind, site=args.site, tag=args.tag)
    if not devices:
        print("Устройств нет")
        return
    width = max(len(d.id) for d in devices)
    for device in devices:
        print("%-*s  %-8s %-15s %-12s %s" % (
            width, device.id, device.kind, device.target or "-",
            device.site or "-", device.name))
    print("\nВсего: %d" % len(devices))


def cmd_search(args):
    vault = open_vault(args, unlock=False)
    devices = vault.search(" ".join(args.query))
    for device in devices:
        print("%s  %s  %s  [%s]" % (
            device.id, device.target or "-", device.name, ", ".join(device.tags) or "-"))
    print("\nНайдено: %d" % len(devices))


def cmd_show(args):
    vault = open_vault(args, unlock=args.secret)
    device = vault.get(args.device_id)
    if not device:
        raise SystemExit("Устройство не найдено: %s" % args.device_id)
    meta = device.to_meta()
    for key, value in meta.items():
        if value not in ("", [], {}, None):
            print("%-12s %s" % (key + ":", ", ".join(value) if isinstance(value, list) else value))
    if args.secret:
        record = vault.secrets.get(device.secret_ref)
        print("\n--- доступы (%s) ---" % device.secret_ref)
        if record:
            for key, value in record.items():
                print("%-16s %s" % (key + ":", value))
        else:
            print("нет сохранённых доступов")
    if device.body.strip():
        print("\n--- заметка ---\n%s" % device.body.rstrip())
    notes = vault.device_notes(device.id)
    if notes:
        print("\nСобранные данные: %d файл(ов), последний %s" % (len(notes), notes[0].name))


def cmd_add(args):
    vault = open_vault(args, unlock=False)
    device = Device(
        name=args.name, kind=args.kind, mgmt_ip=args.ip or "", hostname=args.hostname or "",
        vendor=args.vendor or "", model=args.model or "", site=args.site or "",
        rack=args.rack or "", role=args.role or "", protocol=args.protocol,
        port=args.port, tags=args.tags or "", uplinks=args.uplinks or "",
        body=args.note or "",
    )
    device.id = vault.unique_id(args.name)
    problems = device.validate()
    if problems and not args.force:
        raise SystemExit("Проблемы: %s\n(--force чтобы сохранить как есть)" % "; ".join(problems))
    path = vault.save(device)
    print("Добавлено: %s -> %s" % (device.id, path))
    if args.with_secret:
        vault.unlock(ask_password())
        _prompt_secret(vault, device)


def cmd_rm(args):
    vault = open_vault(args, unlock=True)
    if not vault.get(args.device_id):
        raise SystemExit("Устройство не найдено: %s" % args.device_id)
    if not args.yes:
        answer = input("Удалить %s вместе с доступами? [y/N] " % args.device_id)
        if answer.strip().lower() not in ("y", "yes", "д", "да"):
            raise SystemExit("Отменено")
    vault.delete(args.device_id)
    print("Удалено: %s" % args.device_id)


def _prompt_secret(vault, device):
    print("Доступы для %s (пустая строка — не менять):" % device.secret_ref)
    values = {}
    for field in ("username", "password", "enable_password"):
        if field.endswith("password"):
            values[field] = getpass.getpass("  %s: " % field)
        else:
            values[field] = input("  %s: " % field)
    vault.secrets.put(device.secret_ref, **{k: v for k, v in values.items() if v})
    print("Сохранено.")


def cmd_set_secret(args):
    vault = open_vault(args, unlock=True)
    device = vault.get(args.device_id)
    ref = device.secret_ref if device else args.device_id
    fields = {f: getattr(args, f) for f in SECRET_FIELDS if getattr(args, f, None) is not None}
    if fields:
        vault.secrets.put(ref, **fields)
        print("Обновлено: %s" % ref)
    else:
        _prompt_secret(vault, device or Device(device_id=ref, name=ref))


def cmd_get_secret(args):
    vault = open_vault(args, unlock=True)
    device = vault.get(args.device_id)
    ref = device.secret_ref if device else args.device_id
    record = vault.secrets.get(ref)
    if not record:
        raise SystemExit("Доступов для %s нет" % ref)
    if args.field:
        value = record.get(args.field)
        if value is None:
            raise SystemExit("Поле %s не заполнено" % args.field)
        print(value)
    else:
        for key, value in record.items():
            print("%-16s %s" % (key + ":", value))


def cmd_note(args):
    vault = open_vault(args, unlock=False)
    if not vault.get(args.device_id):
        raise SystemExit("Устройство не найдено: %s" % args.device_id)
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    path = vault.add_note(args.device_id, args.title, text)
    print("Записано: %s" % path)


def cmd_passwd(args):
    vault = open_vault(args, unlock=False)
    old = ask_password("Текущий мастер-пароль: ")
    vault.unlock(old)
    new = getpass.getpass("Новый мастер-пароль: ")
    if new != getpass.getpass("Повторите: "):
        raise SystemExit("Пароли не совпадают")
    vault.secrets.change_password(old, new)
    print("Мастер-пароль изменён.")


def cmd_export(args):
    vault = open_vault(args, unlock=False)
    devices = vault.devices()
    if args.format == "json":
        json.dump([dict(d.to_meta(), id=d.id) for d in devices],
                  sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        columns = ["id", "name", "kind", "mgmt_ip", "hostname", "vendor", "model",
                   "site", "rack", "role", "status", "tags"]
        writer = csv.writer(sys.stdout)
        writer.writerow(columns)
        for device in devices:
            meta = dict(device.to_meta(), id=device.id)
            writer.writerow([
                ", ".join(meta[c]) if isinstance(meta.get(c), list) else meta.get(c, "")
                for c in columns
            ])


def cmd_doctor(args):
    vault = open_vault(args, unlock=False)
    stats = vault.stats()
    print("Устройств: %d" % stats["total"])
    print("По типам: %s" % ", ".join("%s=%d" % kv for kv in stats["by_kind"].items()))
    print("По площадкам: %s" % ", ".join("%s=%d" % kv for kv in stats["by_site"].items()))
    ips = {}
    for device in vault.devices():
        if device.mgmt_ip:
            ips.setdefault(device.mgmt_ip, []).append(device.id)
    duplicates = {ip: ids for ip, ids in ips.items() if len(ids) > 1}
    for ip, ids in duplicates.items():
        print("Дубликат IP %s: %s" % (ip, ", ".join(ids)))
    for device_id, problems in stats["problems"].items():
        print("%s: %s" % (device_id, "; ".join(problems)))
    for device_id, target, found in vault.topology():
        if not found:
            print("%s: аплинк '%s' не найден в хранилище" % (device_id, target))
    if not duplicates and not stats["problems"]:
        print("Проблем не найдено.")


# ------------------------------------------------------------------- парсер
def build_parser():
    parser = argparse.ArgumentParser(
        prog="netvault", description="NetVault — хранилище данных о сетевом оборудовании")
    parser.add_argument("--vault", help="путь к хранилищу (по умолчанию — последнее открытое)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="создать хранилище").set_defaults(func=cmd_init)

    p_list = sub.add_parser("list", help="список устройств")
    p_list.add_argument("--kind", choices=KINDS)
    p_list.add_argument("--site")
    p_list.add_argument("--tag")
    p_list.set_defaults(func=cmd_list)

    p_search = sub.add_parser("search", help="поиск по всем полям и заметкам")
    p_search.add_argument("query", nargs="+")
    p_search.set_defaults(func=cmd_search)

    p_show = sub.add_parser("show", help="карточка устройства")
    p_show.add_argument("device_id")
    p_show.add_argument("--secret", action="store_true", help="показать доступы")
    p_show.set_defaults(func=cmd_show)

    p_add = sub.add_parser("add", help="добавить устройство")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--kind", choices=KINDS, default="switch")
    p_add.add_argument("--ip", dest="ip")
    p_add.add_argument("--hostname")
    p_add.add_argument("--vendor")
    p_add.add_argument("--model")
    p_add.add_argument("--site")
    p_add.add_argument("--rack")
    p_add.add_argument("--role")
    p_add.add_argument("--protocol", default="ssh")
    p_add.add_argument("--port", type=int, default=0)
    p_add.add_argument("--tags", help="через запятую")
    p_add.add_argument("--uplinks", help="через запятую")
    p_add.add_argument("--note", help="текст заметки")
    p_add.add_argument("--with-secret", action="store_true", help="сразу спросить логин и пароль")
    p_add.add_argument("--force", action="store_true")
    p_add.set_defaults(func=cmd_add)

    p_rm = sub.add_parser("rm", help="удалить устройство")
    p_rm.add_argument("device_id")
    p_rm.add_argument("-y", "--yes", action="store_true")
    p_rm.set_defaults(func=cmd_rm)

    p_set = sub.add_parser("set-secret", help="задать доступы")
    p_set.add_argument("device_id")
    for field in SECRET_FIELDS:
        p_set.add_argument("--%s" % field.replace("_", "-"), dest=field)
    p_set.set_defaults(func=cmd_set_secret)

    p_get = sub.add_parser("get-secret", help="показать доступы")
    p_get.add_argument("device_id")
    p_get.add_argument("--field", choices=SECRET_FIELDS)
    p_get.set_defaults(func=cmd_get_secret)

    p_note = sub.add_parser("note", help="приложить заметку к устройству")
    p_note.add_argument("device_id")
    p_note.add_argument("--title", default="note")
    p_note.add_argument("--file", default="-", help="файл или - для stdin")
    p_note.set_defaults(func=cmd_note)

    sub.add_parser("passwd", help="сменить мастер-пароль").set_defaults(func=cmd_passwd)

    p_export = sub.add_parser("export", help="выгрузить инвентарь (без паролей)")
    p_export.add_argument("--format", choices=("csv", "json"), default="csv")
    p_export.set_defaults(func=cmd_export)

    sub.add_parser("doctor", help="проверить хранилище на ошибки").set_defaults(func=cmd_doctor)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (VaultError, CryptoError) as exc:
        raise SystemExit("Ошибка: %s" % exc)
    except KeyboardInterrupt:
        raise SystemExit("\nПрервано")
    return 0


if __name__ == "__main__":
    main()

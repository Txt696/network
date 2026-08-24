#!/usr/bin/env python3
"""
Консольный NetMaster: выполнение команд на устройствах из хранилища NetVault.

Примеры:
    python netmaster/cli.py targets --tag core
    python netmaster/cli.py run --tag core "show version" --save
    python netmaster/cli.py run --device core-sw-01 "show run" --enable
    python netmaster/cli.py ping --site DC1

Мастер-пароль спрашивается интерактивно либо берётся из NETVAULT_PASSWORD.
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netcore import CryptoError, Vault, VaultError  # noqa: E402
from netmaster.core import network_tools, runner  # noqa: E402
from netmaster.core.inventory import Inventory  # noqa: E402
from netvault import appconfig  # noqa: E402


def open_inventory(args, unlock=True):
    vault = Vault(appconfig.resolve_vault_path(args.vault))
    if not vault.is_vault:
        raise SystemExit("Хранилища нет: %s" % vault.path)
    if unlock:
        password = os.environ.get(appconfig.ENV_PASSWORD) or getpass.getpass("Мастер-пароль: ")
        vault.unlock(password)
    return Inventory(vault)


def select_devices(inventory, args):
    devices = inventory.devices(getattr(args, "query", "") or "",
                                kind=args.kind, site=args.site, tag=args.tag)
    if args.device:
        wanted = set(args.device)
        devices = [d for d in devices if d.id in wanted or d.name in wanted]
    return devices


def cmd_targets(args):
    inventory = open_inventory(args, unlock=not args.no_secrets)
    devices = select_devices(inventory, args)
    for device in devices:
        if args.no_secrets:
            print("%-22s %-15s %s" % (device.id, device.target or "-", device.name))
        else:
            target = inventory.target(device)
            print("%-22s %-15s %-10s %s" % (
                target.id, target.host or "-",
                "есть доступ" if target.has_credentials else "НЕТ ДОСТУПОВ", target.name))
    print("\nВсего: %d" % len(devices))


def cmd_run(args):
    inventory = open_inventory(args)
    devices = select_devices(inventory, args)
    targets = inventory.targets(devices)
    runnable = [t for t in targets if t.host and t.has_credentials]
    skipped = [t for t in targets if t not in runnable]
    if not runnable:
        raise SystemExit("Нет устройств с адресом и сохранёнными доступами")
    for target in skipped:
        print("пропущено: %s (нет адреса или доступов)" % target.id, file=sys.stderr)
    if not args.yes and len(runnable) > 1:
        answer = input("Выполнить %d команд(ы) на %d устройствах? [y/N] "
                       % (len(args.commands), len(runnable)))
        if answer.strip().lower() not in ("y", "yes", "д", "да"):
            raise SystemExit("Отменено")

    def progress(done, total, result):
        status = "ok" if result.ok else "ОШИБКА: %s" % result.error
        print("[%d/%d] %s — %s" % (done, total, result.name, status), file=sys.stderr)

    results = runner.run_on_many(runnable, args.commands, workers=args.workers,
                                 progress=progress, use_enable=args.enable,
                                 timeout=args.timeout)
    for result in results:
        print("\n===== %s (%s) =====" % (result.name, result.host))
        print(result.output.strip() or ("ошибка: %s" % result.error))
    print("\n" + runner.summarize(results), file=sys.stderr)
    if args.save:
        title = args.commands[0].replace(" ", "-")[:40]
        saved = runner.save_results(inventory, results, title)
        print("Сохранено в хранилище: %d файл(ов)" % len(saved), file=sys.stderr)


def cmd_ping(args):
    inventory = open_inventory(args, unlock=False)
    devices = select_devices(inventory, args)
    tool = network_tools.PingTool()
    unreachable = 0
    for device in devices:
        if not device.target:
            print("%-22s нет адреса" % device.id)
            continue
        result = tool.ping(device.target, count=args.count)
        stats = result.get("stats", {})
        ok = result.get("success")
        unreachable += 0 if ok else 1
        print("%-22s %-15s %-12s loss=%s%% avg=%s" % (
            device.id, device.target, "доступен" if ok else "НЕДОСТУПЕН",
            stats.get("loss", "?"), stats.get("avg", "?")))
    print("\nНедоступно: %d из %d" % (unreachable, len(devices)))


def build_parser():
    parser = argparse.ArgumentParser(prog="netmaster", description=__doc__)
    parser.add_argument("--vault", help="путь к хранилищу")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_filters(sub_parser):
        sub_parser.add_argument("--device", action="append", help="id устройства (можно несколько)")
        sub_parser.add_argument("--kind")
        sub_parser.add_argument("--site")
        sub_parser.add_argument("--tag")
        sub_parser.add_argument("--query", help="строка поиска как в NetVault")

    p_targets = sub.add_parser("targets", help="какие устройства попадут под фильтр")
    add_filters(p_targets)
    p_targets.add_argument("--no-secrets", action="store_true",
                           help="не открывать хранилище паролей")
    p_targets.set_defaults(func=cmd_targets)

    p_run = sub.add_parser("run", help="выполнить команды по SSH")
    add_filters(p_run)
    p_run.add_argument("commands", nargs="+", help="команды (каждая — отдельный аргумент)")
    p_run.add_argument("--enable", action="store_true", help="войти в enable перед командами")
    p_run.add_argument("--save", action="store_true", help="сохранить вывод в хранилище")
    p_run.add_argument("--workers", type=int, default=8)
    p_run.add_argument("--timeout", type=int, default=30)
    p_run.add_argument("-y", "--yes", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_ping = sub.add_parser("ping", help="проверить доступность")
    add_filters(p_ping)
    p_ping.add_argument("--count", type=int, default=2)
    p_ping.set_defaults(func=cmd_ping)
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

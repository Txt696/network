#!/usr/bin/env python3
"""
NetWatch — мониторинг компьютеров домена.

Запуск:  python netwatch/main.py [--host 0.0.0.0] [--port 8770] [--interval 120]
         python netwatch/main.py --once            одна проверка, таблица в консоль
         python netwatch/main.py --computers pcs.txt   список из файла, а не из AD

Берёт компьютеры из Active Directory, постоянно проверяет, отвечают ли они,
и показывает на веб-странице, кто на связи, кто не отвечает, кто пропал
и у кого сломалась связь с доменом. Вторая вкладка — журналы событий
Windows с контроллеров домена: блокировки, неудачные входы, сломанное
доверие, синие экраны — с пояснением, что каждое событие значит.
"""

import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def default_data_dir():
    """Куда класть состояние: общая папка программ на Windows, домашняя — на остальных."""
    if sys.platform == "win32" and os.environ.get("PROGRAMDATA"):
        return Path(os.environ["PROGRAMDATA"]) / "NetWatch"
    return Path.home() / ".netwatch"


def selftest():
    """Для собранного .exe: модули на месте и страница попала в сборку."""
    from netwatch import catalog, eventlogs, monitor, probe, server  # noqa: F401
    page = server.STATIC / "watch.html"
    print("Python %s" % sys.version.split()[0])
    print("  [%s] страница мониторинга — %s" % ("есть" if page.exists() else "НЕТ ", page))
    print("Готово: сборка работоспособна" if page.exists() else "Сборка неполная")
    return 0 if page.exists() else 1


def print_table(snapshot):
    labels = snapshot["labels"]
    rows = [(rec["name"], labels.get(rec["status"], rec["status"]), rec.get("address") or "",
             time.strftime("%d.%m %H:%M", time.localtime(rec["last_seen"]))
             if rec.get("last_seen") else "—", rec.get("how") or "")
            for rec in snapshot["computers"]]
    header = ("Компьютер", "Статус", "IP", "Отвечал", "Как проверен")
    widths = [max(len(str(row[i])) for row in rows + [header]) for i in range(len(header))]
    for row in [header] + rows:
        print("  ".join(str(value).ljust(width) for value, width in zip(row, widths)))
    print()
    print(" · ".join("%s: %d" % (labels[key], count)
                     for key, count in snapshot["counts"].items()))
    if snapshot["cycle"].get("error"):
        print("Ошибка: %s" % snapshot["cycle"]["error"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="NetWatch — мониторинг компьютеров домена")
    parser.add_argument("--computers", metavar="ФАЙЛ",
                        help="список компьютеров из файла вместо Active Directory")
    parser.add_argument("--base", default="", metavar="DN",
                        help="только это подразделение AD, например "
                             "\"OU=Компьютеры,DC=corp,DC=local\"")
    parser.add_argument("--interval", type=int, default=120,
                        help="пауза между проверками, секунд (по умолчанию 120)")
    parser.add_argument("--lost-days", type=float, default=3,
                        help="через сколько дней без ответа ПК считается пропавшим (3)")
    parser.add_argument("--stale-days", type=float, default=45,
                        help="через сколько дней без входа в домен связь с доменом "
                             "считается потерянной (45)")
    parser.add_argument("--event-hosts", default="dc", metavar="КТО",
                        help="с кого собирать журналы событий: dc — контроллеры домена "
                             "(по умолчанию), all — все компьютеры на связи, local — эта "
                             "машина, или имена через запятую; none — не собирать")
    parser.add_argument("--events-interval", type=int, default=300,
                        help="пауза между сборами журналов, секунд (по умолчанию 300)")
    parser.add_argument("--events-hours", type=float, default=24,
                        help="за сколько часов брать события при первом запуске (24)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="адрес страницы; 0.0.0.0 — открыть для других ПК в сети")
    parser.add_argument("--port", type=int, default=8770, help="порт (по умолчанию 8770)")
    parser.add_argument("--data", metavar="ПАПКА", help="где хранить состояние и журнал")
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    parser.add_argument("--once", action="store_true",
                        help="проверить один раз, вывести таблицу и выйти")
    parser.add_argument("--selftest", action="store_true",
                        help="проверить сборку и выйти (для собранного .exe)")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    from netwatch import directory, eventlogs
    from netwatch.monitor import Monitor
    from netwatch.server import make_server

    data_dir = Path(args.data).expanduser() if args.data else default_data_dir()
    monitor = Monitor(directory.loader(args.computers, args.base), data_dir / "state.json",
                      interval=max(10, args.interval), lost_days=args.lost_days,
                      stale_days=args.stale_days,
                      source=directory.describe_source(args.computers, args.base))

    if args.once:
        monitor.run_once()
        print_table(monitor.snapshot())
        return 1 if monitor.cycle.get("error") and not monitor.records else 0

    collector = None
    if args.event_hosts.strip().lower() != "none":
        collector = eventlogs.EventCollector(
            eventlogs.host_picker(monitor, args.event_hosts), data_dir / "eventlog.json",
            interval=max(30, args.events_interval), hours=args.events_hours,
            spec=args.event_hosts)

    try:
        server = make_server(monitor, args.host, args.port, collector)
    except OSError as exc:
        raise SystemExit("Не занять порт %d: %s" % (args.port, exc))

    shown = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    address = "http://%s:%d/" % (shown, server.server_address[1])
    print("Мониторинг: %s" % address)
    print("Источник: %s" % monitor.source)
    print("Журналы событий: %s" % (args.event_hosts if collector else "не собираются"))
    print("Состояние: %s" % data_dir)
    print("Остановить: Ctrl+C")
    threading.Thread(target=monitor.run_forever, daemon=True).start()
    if collector:
        # Контроллеры домена известны только после первого чтения AD.
        threading.Thread(target=lambda: monitor.first_pass.wait() and collector.run_forever(),
                         daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(address,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")
    finally:
        monitor.stop()
        if collector:
            collector.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

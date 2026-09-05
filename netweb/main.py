#!/usr/bin/env python3
"""
NetMap — карта сети в браузере.

Запуск:  python netweb/main.py [--vault ПУТЬ] [--port 8765] [--no-browser]

Показывает устройства из хранилища NetVault, сгруппированные по регионам,
и связи между ними с портами. Только просмотр: пароли не читаются.
"""

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netvault import appconfig  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="NetMap — карта сети")
    parser.add_argument("--vault", help="путь к хранилищу NetVault")
    parser.add_argument("--port", type=int, default=8765, help="порт (по умолчанию 8765)")
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    parser.add_argument("--selftest", action="store_true",
                        help="проверить сборку и выйти (для собранного .exe)")
    args = parser.parse_args(argv)

    if args.selftest:
        from netcore import selftest
        return selftest.run()

    from netcore import VaultError
    from netweb.server import make_server

    path = appconfig.resolve_vault_path(args.vault)
    try:
        server = make_server(path, args.port)
    except VaultError as exc:
        raise SystemExit("Не открыть хранилище: %s" % exc)
    except OSError as exc:
        raise SystemExit("Не занять порт %d: %s" % (args.port, exc))

    address = "http://127.0.0.1:%d/" % server.server_address[1]
    print("Карта сети: %s" % address)
    print("Хранилище: %s" % path)
    print("Остановить: Ctrl+C")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(address,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

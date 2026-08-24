#!/usr/bin/env python3
"""
NetVault — приложение-хранилище данных о серверах, свитчах и роутерах.

Заметки хранятся в Markdown (папку можно открыть в Obsidian),
пароли — в зашифрованном файле secrets.enc.

Запуск:  python netvault/main.py [--vault ПУТЬ]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netvault import APP_NAME, __version__, appconfig  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="%s %s" % (APP_NAME, __version__))
    parser.add_argument("--vault", help="путь к хранилищу")
    parser.add_argument("--selftest", action="store_true",
                        help="проверить сборку и выйти (для собранного .exe)")
    args = parser.parse_args(argv)

    if args.selftest:
        from netcore import selftest
        return selftest.run()

    try:
        import tkinter as tk
    except ImportError:
        raise SystemExit(
            "Не найден tkinter. На Windows он входит в установщик Python "
            "(галочка «tcl/tk and IDLE»), на Linux: sudo apt install python3-tk.\n"
            "Без графики можно работать через консоль: python netvault/cli.py --help")

    from netvault.gui.main_window import MainWindow
    from netvault.gui.unlock_dialog import UnlockDialog

    root = tk.Tk()
    root.title("%s %s — данные сетевого оборудования" % (APP_NAME, __version__))
    root.geometry("1180x760")
    root.minsize(900, 600)

    vault = UnlockDialog.ask(root, appconfig.resolve_vault_path(args.vault))
    if vault is None:
        root.destroy()
        return 0

    MainWindow(root, vault)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

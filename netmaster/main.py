#!/usr/bin/env python3
"""
NetMaster — управление сетевым оборудованием по данным из NetVault.

Запуск:  python netmaster/main.py [--vault ПУТЬ] [--device ID]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netvault import appconfig  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="NetMaster — управление оборудованием")
    parser.add_argument("--vault", help="путь к хранилищу NetVault")
    parser.add_argument("--device", help="сразу подключиться к устройству с этим id")
    parser.add_argument("--selftest", action="store_true",
                        help="проверить сборку и выйти (для собранного .exe)")
    args = parser.parse_args(argv)

    if args.selftest:
        from netcore import selftest
        return selftest.run(selftest.GUI + selftest.SSH)

    try:
        import tkinter as tk
    except ImportError:
        raise SystemExit(
            "Не найден tkinter. На Windows он входит в установщик Python "
            "(галочка «tcl/tk and IDLE»), на Linux: sudo apt install python3-tk.\n"
            "Без графики: python netmaster/cli.py --help")

    from netmaster.gui.main_window import MainWindow
    from netvault.gui.unlock_dialog import UnlockDialog

    root = tk.Tk()
    root.title("NetMaster — управление сетевым оборудованием")
    root.geometry("1280x800")
    root.minsize(1000, 650)

    vault = UnlockDialog.ask(root, appconfig.resolve_vault_path(args.vault))
    if vault is None:
        root.destroy()
        return 0

    MainWindow(root, vault, autoconnect_device=args.device)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

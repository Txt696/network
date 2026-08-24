"""
Запуск соседнего приложения: NetVault открывает NetMaster и наоборот.

Из исходников запускается скрипт текущим Python. В собранной программе
исходников нет вовсе, а `sys.executable` — это сам exe, поэтому ищем
соседний exe в той же папке. Отсюда правило для пользователя:
NetVault.exe и NetMaster.exe должны лежать рядом.
"""

import os
import sys
from pathlib import Path


def is_frozen():
    """Программа собрана в самостоятельный файл (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def exe_name(app_name):
    return app_name + (".exe" if os.name == "nt" else "")


def neighbour_exe(app_name, executable=None):
    """Путь к соседней собранной программе рядом с текущей."""
    executable = Path(executable or sys.executable).resolve()
    return executable.parent / exe_name(app_name)


def command_for(app_name, script, frozen=None, executable=None, source_root=None):
    """
    Чем запускать соседнее приложение.

    Возвращает (команда, рабочая папка) или None, если запускать нечего.
    app_name — имя собранной программы («NetMaster»),
    script — путь скрипта от корня исходников («netmaster/main.py»).
    Остальные параметры нужны тестам, чтобы разыграть оба режима.
    """
    frozen = is_frozen() if frozen is None else frozen
    if frozen:
        neighbour = neighbour_exe(app_name, executable)
        if neighbour.exists():
            return [str(neighbour)], str(neighbour.parent)
        return None
    root = Path(source_root or Path(__file__).resolve().parents[1])
    path = root / script
    if path.exists():
        return [executable or sys.executable, str(path)], str(root)
    return None


def not_found_message(app_name, frozen=None, executable=None):
    """Что показать пользователю, если соседнее приложение не нашлось."""
    frozen = is_frozen() if frozen is None else frozen
    if frozen:
        return ("Не нашёл %s рядом с этой программой.\n\n"
                "Положите %s и %s в одну папку — тогда кнопки перехода между "
                "ними будут работать.\n\nИскал здесь: %s"
                % (exe_name(app_name), exe_name(app_name),
                   Path(executable or sys.executable).name,
                   neighbour_exe(app_name, executable).parent))
    return ("Не нашёл %s: рядом нет ни собранной программы, ни исходников."
            % app_name)

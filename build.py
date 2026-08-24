#!/usr/bin/env python3
"""
Сборка NetVault и NetMaster в самостоятельные программы.

    python build.py            собрать обе, каждая — один файл
    python build.py netmaster  собрать только NetMaster
    python build.py --folder   папкой вместо одного файла (запускается быстрее)

На Windows получатся dist\\NetVault.exe и dist\\NetMaster.exe — их можно
скопировать на другой компьютер, Python там не нужен. Собирать надо на той
системе, для которой нужна программа: exe собирается только на Windows.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
VERIFY_TIMEOUT = 120  # сколько ждём ответа от собранной программы на --selftest
WORK = ROOT / "build"

APPS = {
    "netvault": ("NetVault", ROOT / "netvault" / "main.py", ROOT / "packaging" / "netvault.ico"),
    "netmaster": ("NetMaster", ROOT / "netmaster" / "main.py", ROOT / "packaging" / "netmaster.ico"),
}


def build(key, onefile=True):
    name, entry, icon = APPS[key]
    print("\n=== Собираю %s ===" % name)
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--noupx",
        "--onefile" if onefile else "--onedir",
        "--name", name,
        "--paths", str(ROOT),
        "--distpath", str(DIST), "--workpath", str(WORK), "--specpath", str(WORK),
    ]
    if icon.exists():
        args += ["--icon", str(icon)]
    args.append(str(entry))
    subprocess.check_call(args)
    return DIST / (name + (".exe" if sys.platform == "win32" else ""))


def verify(path):
    """Запустить собранную программу с --selftest: работает ли она вообще."""
    if not path.exists():
        print("!! не нашёл %s" % path)
        return False
    try:
        result = subprocess.run([str(path), "--selftest"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=VERIFY_TIMEOUT)
    except subprocess.TimeoutExpired:
        print("%s: не ответила за %d с — проверка прервана" % (path.name, VERIFY_TIMEOUT))
        return False
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output)
    ok = result.returncode == 0
    print("%s: %s" % (path.name, "работает" if ok else "НЕ РАБОТАЕТ"))
    return ok


def use_utf8():
    """Печатать по-русски можно и в консоли Windows, где по умолчанию cp1252."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv=None):
    use_utf8()
    parser = argparse.ArgumentParser(description="Сборка NetVault и NetMaster")
    parser.add_argument("apps", nargs="*", metavar="ПРОГРАММА",
                        help="netvault и/или netmaster (по умолчанию обе)")
    parser.add_argument("--folder", action="store_true",
                        help="собрать папкой, а не одним файлом (запуск быстрее)")
    parser.add_argument("--no-check", action="store_true",
                        help="не запускать проверку после сборки")
    args = parser.parse_args(argv)

    apps = [name.lower() for name in args.apps] or list(APPS)
    unknown = [name for name in apps if name not in APPS]
    if unknown:
        raise SystemExit("Не знаю таких программ: %s. Есть: %s"
                         % (", ".join(unknown), ", ".join(APPS)))

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("Нет PyInstaller. Установите: pip install -r requirements.txt")

    built, ok = [], True
    for key in apps:
        built.append(build(key, onefile=not args.folder))
    if not args.no_check:
        print("\n=== Проверка ===")
        ok = all(verify(path) for path in built)

    shutil.rmtree(WORK, ignore_errors=True)
    print("\nГотово. Файлы в папке: %s" % DIST)
    for path in built:
        print("  %s" % path.name)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

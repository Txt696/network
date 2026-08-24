"""
Проверка, что приложение собрано целиком и запустится.

Нужна для собранных .exe: графическое окно ничего не печатает, поэтому
после сборки exe запускают с ключом --selftest и смотрят код возврата.
"""

import sys

# Что нужно любой из программ. Своё каждая передаёт через extra:
# NetVault обходится без paramiko — по SSH ходит только NetMaster.
CHECKS = (
    ("tkinter", "графический интерфейс"),
    ("cryptography", "шифрование хранилища"),
    ("netcore", "ядро NetVault"),
)

SSH = (("paramiko", "подключение по SSH и SFTP"),)

OPTIONAL = (("argon2", "усиленный KDF (необязателен, иначе scrypt)"),)


def check(extra=()):
    """Вернуть (всё_на_месте, строки отчёта)."""
    lines = ["Python %s" % sys.version.split()[0]]
    ok = True
    for name, what in tuple(CHECKS) + tuple(extra):
        try:
            __import__(name)
            lines.append("  [есть] %-14s — %s" % (name, what))
        except ImportError as exc:
            ok = False
            lines.append("  [НЕТ ] %-14s — %s (%s)" % (name, what, exc))
    for name, what in OPTIONAL:
        found = "есть" if _has(name) else "нет "
        lines.append("  [%s] %-14s — %s" % (found, name, what))
    lines.append("Готово: сборка работоспособна" if ok else "Сборка неполная")
    return ok, lines


def _has(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def run(extra=()):
    """Напечатать отчёт и вернуть код возврата для sys.exit."""
    ok, lines = check(extra)
    print("\n".join(lines))
    return 0 if ok else 1

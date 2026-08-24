"""
Разбор того, что присылает устройство, и того, что мы шлём в ответ.

Модуль без tkinter: терминал печатает прямо в окно, эхо приходит от
устройства, поэтому надо чистить управляющие последовательности и
обрабатывать забой (`\b`) и возврат каретки (`\r`) — иначе в окне мусор.
"""

import re
import sys

# Управляющие последовательности: цвета, перемещения курсора, заголовок окна.
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_SINGLE = re.compile(r"\x1b[()][B0]|\x1b[=>]")

CONTROL_MASK = 0x4
# Alt по-разному помечается на разных системах: на Windows это отдельный бит,
# а Mod1 (0x8) там занят NumLock — если проверять 0x8, с включённым NumLock
# ввод в терминал перестаёт работать.
ALT_MASK = 0x20000 if sys.platform == "win32" else 0x8

# Клавиша Tk -> что уходит в канал.
KEYS = {
    "Return": "\r",
    "KP_Enter": "\r",
    "BackSpace": "\x7f",
    "Delete": "\x1b[3~",
    "Tab": "\t",
    "Escape": "\x1b",
    "Up": "\x1b[A",
    "Down": "\x1b[B",
    "Right": "\x1b[C",
    "Left": "\x1b[D",
    "Home": "\x1b[H",
    "End": "\x1b[F",
    "Prior": "\x1b[5~",
    "Next": "\x1b[6~",
}


def clean(text):
    """Убрать управляющие последовательности, оставить текст и \\b, \\r, \\n."""
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    text = _SINGLE.sub("", text)
    text = text.replace("\x1b", "").replace("\x00", "").replace("\x07", "")
    return text.replace("\r\n", "\n")


def ctrl_code(letter):
    """Control-код для Ctrl+буква: Ctrl+C -> \\x03, Ctrl+D -> \\x04."""
    letter = (letter or "").lower()
    if len(letter) == 1 and "a" <= letter <= "z":
        return chr(ord(letter) - ord("a") + 1)
    return ""


def apply_edits(line, text):
    """
    Дописать `text` к последней строке `line` с учётом забоя и возврата каретки.

    Возвращает (готовые_строки, текущая_строка): готовые строки уже завершены
    переводом строки, текущую строку терминал держит как «последнюю».
    """
    done = []
    current = line
    for char in text:
        if char == "\n":
            done.append(current + "\n")
            current = ""
        elif char == "\r":
            current = ""
        elif char == "\b":
            current = current[:-1]
        else:
            current += char
    return done, current


def key_data(keysym, char, state):
    """
    Что отправить на устройство по нажатой клавише.

    None — событие не наше (нажат Alt), пусть его обработает система.
    Пустая строка — отправлять нечего (Shift, Caps и прочие модификаторы).
    """
    if state & ALT_MASK:
        return None
    if state & CONTROL_MASK:
        return ctrl_code(keysym)
    data = KEYS.get(keysym)
    return char if data is None else data

"""
Разбор того, что присылает устройство, и того, что мы шлём в ответ.

Модуль без tkinter: терминал печатает прямо в окно, эхо приходит от
устройства, поэтому надо чистить управляющие последовательности и
обрабатывать забой (`\b`) и возврат каретки (`\r`) — иначе в окне мусор.
"""

import re

# Управляющие последовательности: цвета, перемещения курсора, заголовок окна.
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_SINGLE = re.compile(r"\x1b[()][B0]|\x1b[=>]")

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

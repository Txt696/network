"""
Чтение и запись YAML-фронтматтера Markdown-заметок.

Реализовано подмножество YAML, достаточное для инвентаря и полностью
совместимое с Obsidian: скаляры, списки (блочные и inline), вложенные
словари на один уровень. Собственный парсер выбран, чтобы приложение
работало на чистом Python без внешних зависимостей.
"""

import re

DELIMITER = "---"
_NEEDS_QUOTES = re.compile(r"^\s|\s$|^[\[\]{}>|*&!%@`#-]|:\s|^$")
_BOOL_LIKE = {"true", "false", "yes", "no", "null", "~", "on", "off"}


def _parse_scalar(text):
    text = text.strip()
    if not text:
        return ""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_parse_scalar(x) for x in inner.split(",")] if inner else []
    low = text.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def _format_scalar(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if _NEEDS_QUOTES.search(text) or text.lower() in _BOOL_LIKE or re.fullmatch(r"-?\d+(\.\d+)?", text):
        return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')
    return text


def parse(text):
    """Вернуть (метаданные, тело). Если фронтматтера нет — ({}, text)."""
    if not text.startswith(DELIMITER):
        return {}, text
    lines = text.splitlines()
    if lines[0].strip() != DELIMITER:
        return {}, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == DELIMITER)
    except StopIteration:
        return {}, text

    meta = {}
    key = None
    container = None  # список или словарь, который сейчас наполняем
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = raw[0] in " \t"
        stripped = raw.strip()

        if indented and key is not None:
            if stripped.startswith("- "):
                if not isinstance(container, list):
                    container = meta[key] = []
                container.append(_parse_scalar(stripped[2:]))
            elif ":" in stripped:
                if not isinstance(container, dict):
                    container = meta[key] = {}
                sub_key, _, sub_val = stripped.partition(":")
                container[sub_key.strip()] = _parse_scalar(sub_val)
            continue

        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        meta[key] = _parse_scalar(value) if value else ""
        container = meta[key] if isinstance(meta[key], (list, dict)) else None

    body = "\n".join(lines[end + 1:])
    return meta, body.lstrip("\n")


def dump(meta, body="", key_order=()):
    """Собрать Markdown-файл из метаданных и тела."""
    ordered = [k for k in key_order if k in meta]
    ordered += [k for k in meta if k not in ordered]

    lines = [DELIMITER]
    for key in ordered:
        value = meta[key]
        if isinstance(value, (list, tuple)):
            if not value:
                lines.append("%s: []" % key)
            else:
                lines.append("%s:" % key)
                lines.extend("  - %s" % _format_scalar(v) for v in value)
        elif isinstance(value, dict):
            if not value:
                lines.append("%s: {}" % key)
            else:
                lines.append("%s:" % key)
                lines.extend("  %s: %s" % (k, _format_scalar(v)) for k, v in value.items())
        else:
            lines.append("%s: %s" % (key, _format_scalar(value)))
    lines.append(DELIMITER)
    text = "\n".join(lines) + "\n"
    if body:
        text += "\n" + body.rstrip("\n") + "\n"
    return text

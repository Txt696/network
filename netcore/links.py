"""
Связи между устройствами: кто в какой порт кому воткнут.

Пишутся в заметке устройства, в поле `uplinks`:

    uplinks:
      - "Gi1/0/48 -> balkan-sw-01:Gi1/0/1"   # свой порт -> сосед:его порт
      - balkan-sw-01                          # просто сосед, без портов

Старая запись без портов продолжает работать — она и сейчас лежит
в заметках, ломать её нельзя.
"""

import re

ARROW = re.compile(r"\s*(?:->|-->|→|=>)\s*")


def parse(entry):
    """Разобрать строку связи в (свой_порт, сосед, порт_соседа)."""
    text = str(entry or "").strip()
    if not text:
        return "", "", ""
    local_port, _, remote = _split_arrow(text)
    peer, peer_port = _split_peer(remote)
    return local_port, peer, peer_port


def _split_arrow(text):
    parts = ARROW.split(text, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), True, parts[1].strip()
    return "", False, text


def _split_peer(text):
    """`balkan-sw-01:Gi1/0/1` -> (устройство, порт). Двоеточия в имени нет."""
    if ":" in text:
        peer, _, port = text.partition(":")
        return peer.strip(), port.strip()
    return text.strip(), ""


def format(local_port, peer, peer_port=""):
    """Собрать строку связи обратно — как её увидит пользователь в заметке."""
    peer = (peer or "").strip()
    if not peer:
        return ""
    target = "%s:%s" % (peer, peer_port.strip()) if peer_port else peer
    local_port = (local_port or "").strip()
    return "%s -> %s" % (local_port, target) if local_port else target


def describe(local_port, peer_port):
    """Подпись для линии на карте: «Gi1/0/48 — Gi1/0/1»."""
    if local_port and peer_port:
        return "%s — %s" % (local_port, peer_port)
    return local_port or peer_port or ""

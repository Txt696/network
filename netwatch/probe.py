"""
Проверка, на связи ли компьютер.

Одного ping мало: брандмауэр Windows по умолчанию эхо-запросы режет.
Поэтому сначала стучимся на порты, которые у ПК в домене открыты почти
всегда (SMB, RPC, RDP), и только потом пингуем. Отказ в соединении
тоже ответ: порт закрыт, но компьютер включён и его отправил.
"""

import socket
import subprocess
import sys
from dataclasses import dataclass

from netwatch.powershell import NO_WINDOW

TCP_PORTS = (445, 135, 3389)
TIMEOUT = 1.0


@dataclass
class Result:
    alive: bool
    address: str = ""
    how: str = ""     # «TCP 445», «ping», «нет в DNS», «не отвечает»


def check(host, ports=TCP_PORTS, timeout=TIMEOUT):
    try:
        address = socket.gethostbyname(host)
    except (OSError, UnicodeError):
        return Result(False, "", "нет в DNS")
    for port in ports:
        if tcp_alive(address, port, timeout):
            return Result(True, address, "TCP %d" % port)
    if ping(address, timeout):
        return Result(True, address, "ping")
    return Result(False, address, "не отвечает")


def tcp_alive(address, port, timeout=TIMEOUT):
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except ConnectionRefusedError:
        return True
    except OSError:
        return False


def ping(address, timeout=TIMEOUT):
    if sys.platform == "win32":
        command = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), address]
    else:
        command = ["ping", "-c", "1", "-W", str(max(1, round(timeout))), address]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout + 5,
                                creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    # Windows возвращает 0 и на «Заданный узел недоступен» от маршрутизатора,
    # поэтому верим только настоящему ответу — в нём есть TTL.
    return result.returncode == 0 and b"ttl=" in result.stdout.lower()

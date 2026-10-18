"""
Запуск PowerShell-скрипта и получение ответа в целости.

Скрипт отдаёт результат одной строкой base64 (функция Say): так ни
кодировка консоли (cp866), ни CLIXML, которым PowerShell иногда оборачивает
вывод, его не испортят. Тело скрипта должно закончиться строкой
Say 'OK:' (ConvertTo-Json ...); любая ошибка вернётся как текст.
"""

import base64
import json
import subprocess

# Без этого флага собранная программа мигала бы чёрным окном консоли
# на каждый вызов PowerShell и ping.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PRELUDE = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Say($prefix, $text) {
  [Console]::Out.WriteLine($prefix + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($text)))
}
try {
__BODY__
} catch {
  Say 'ERR:' $_.Exception.Message
  exit 1
}
"""


class PowerShellError(Exception):
    """Скрипт не отработал; в тексте — сообщение PowerShell."""


class NoPowerShell(PowerShellError):
    """PowerShell на этой системе нет (не Windows)."""


def quote(text):
    """Строка PowerShell в одинарных кавычках: внутри они удваиваются."""
    return "'" + str(text).replace("'", "''") + "'"


def run(body, timeout, run=subprocess.run):
    """Выполнить тело скрипта и вернуть разобранный JSON-ответ."""
    script = PRELUDE.replace("__BODY__", body)
    command = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode("ascii")]
    try:
        result = run(command, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except FileNotFoundError:
        raise NoPowerShell("PowerShell не найден")
    except subprocess.TimeoutExpired:
        raise PowerShellError("не ответил за %d с" % timeout)
    return unpack(result.stdout)


def unpack(stdout):
    lines = [line.strip() for line in stdout.decode("ascii", "replace").splitlines()]
    answer = next((line for line in reversed(lines) if line[:3] in ("OK:", "ERR")), "")
    if not answer:
        raise PowerShellError("PowerShell ничего не вернул")
    try:
        text = base64.b64decode(answer.split(":", 1)[1]).decode("utf-8", "replace")
        if answer.startswith("ERR"):
            raise PowerShellError(text.strip())
        return json.loads(text)
    except ValueError as exc:  # обрезанный base64 или JSON
        raise PowerShellError("Непонятный ответ PowerShell: %s" % exc)

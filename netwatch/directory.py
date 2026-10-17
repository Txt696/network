"""
Откуда берётся список компьютеров: Active Directory или простой файл.

AD читается через PowerShell и ADSI ([adsisearcher]) — это есть в любой
Windows, вошедшей в домен, без RSAT и без модуля ActiveDirectory. Права
нужны обычные: объекты компьютеров может читать любая доменная учётная
запись, в том числе SYSTEM на машине в домене (в AD она ходит как
учётная запись самого компьютера).
"""

import base64
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

FILETIME_EPOCH = 11644473600  # секунд между 1601-01-01 и 1970-01-01
FILETIME_NEVER = 0x7FFFFFFFFFFFFFFF
ACCOUNTDISABLE = 0x2  # бит userAccountControl «учётная запись отключена»
AD_TIMEOUT = 180

# Без этого флага собранная программа мигала бы чёрным окном консоли
# на каждый вызов PowerShell и ping.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

AD_FIELDS = ("name", "dnshostname", "operatingsystem", "useraccountcontrol",
             "lastlogontimestamp", "pwdlastset", "distinguishedname", "description")

# Ответ отдаётся одной строкой base64: так ни кодировка консоли (cp866),
# ни CLIXML, которым PowerShell иногда оборачивает вывод, его не испортят.
AD_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Say($prefix, $text) {
  [Console]::Out.WriteLine($prefix + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($text)))
}
try {
  $searcher = [adsisearcher]'(objectCategory=computer)'
  $base = '__BASE__'
  if ($base) { $searcher.SearchRoot = [adsi]('LDAP://' + $base) }
  $searcher.PageSize = 1000
  foreach ($field in '__FIELDS__'.Split(',')) { [void]$searcher.PropertiesToLoad.Add($field) }
  $rows = foreach ($result in $searcher.FindAll()) {
    $row = @{}
    foreach ($field in $searcher.PropertiesToLoad) {
      $values = $result.Properties[$field]
      if ($values.Count) { $row[$field] = [string]$values[0] }
    }
    $row
  }
  Say 'OK:' (ConvertTo-Json -InputObject @($rows) -Compress)
} catch {
  Say 'ERR:' $_.Exception.Message
  exit 1
}
"""


class DirectoryError(Exception):
    """Список компьютеров получить не удалось."""


@dataclass
class Computer:
    name: str
    host: str = ""            # что проверять: DNS-имя или адрес
    os: str = ""
    ou: str = ""              # подразделение, например «Ашхабад/Бухгалтерия»
    description: str = ""
    enabled: bool = True
    last_logon: Optional[float] = None    # lastLogonTimestamp, unix-время
    pwd_last_set: Optional[float] = None  # когда ПК последний раз менял пароль в домене

    def __post_init__(self):
        self.host = self.host or self.name


# ----------------------------------------------------------------- AD
def from_active_directory(base="", run=subprocess.run):
    """Компьютеры из AD. base — DN подразделения, если нужен не весь домен."""
    script = (AD_SCRIPT.replace("__BASE__", base.replace("'", "''"))
              .replace("__FIELDS__", ",".join(AD_FIELDS)))
    command = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode("ascii")]
    try:
        result = run(command, capture_output=True, timeout=AD_TIMEOUT, creationflags=NO_WINDOW)
    except FileNotFoundError:
        raise DirectoryError(
            "PowerShell не найден. Список из Active Directory читается на Windows-машине "
            "в домене; на этой системе укажите список компьютеров файлом: --computers ФАЙЛ")
    except subprocess.TimeoutExpired:
        raise DirectoryError("AD не ответил за %d с" % AD_TIMEOUT)
    return parse_ad_output(result.stdout)


def parse_ad_output(stdout):
    lines = [line.strip() for line in stdout.decode("ascii", "replace").splitlines()]
    answer = next((line for line in reversed(lines) if line[:3] in ("OK:", "ERR")), "")
    if not answer:
        raise DirectoryError("PowerShell ничего не вернул")
    try:
        text = base64.b64decode(answer.split(":", 1)[1]).decode("utf-8", "replace")
        if answer.startswith("ERR"):
            raise DirectoryError("Ошибка запроса к AD: %s" % text.strip())
        rows = json.loads(text)
    except ValueError as exc:  # обрезанный base64 или JSON
        raise DirectoryError("Непонятный ответ PowerShell: %s" % exc)
    rows = [rows] if isinstance(rows, dict) else rows
    return [computer_from_ad(row) for row in rows if isinstance(row, dict) and row.get("name")]


def computer_from_ad(row):
    flags = int(row.get("useraccountcontrol") or 0)
    return Computer(
        name=row["name"],
        host=row.get("dnshostname", ""),
        os=row.get("operatingsystem", ""),
        ou=ou_from_dn(row.get("distinguishedname", "")),
        description=row.get("description", ""),
        enabled=not flags & ACCOUNTDISABLE,
        last_logon=filetime(row.get("lastlogontimestamp")),
        pwd_last_set=filetime(row.get("pwdlastset")),
    )


def filetime(value):
    """FILETIME из AD (сотни наносекунд с 1601 года) → unix-время или None."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    if value <= 0 or value >= FILETIME_NEVER:
        return None
    return value / 10_000_000 - FILETIME_EPOCH


def ou_from_dn(dn):
    """CN=PC01,OU=Бухгалтерия,OU=Ашхабад,DC=corp,DC=local → Ашхабад/Бухгалтерия."""
    parts = [part.strip() for part in re.split(r"(?<!\\),", dn) if part.strip()]
    names = [part.split("=", 1)[1].replace("\\,", ",") for part in parts[1:]
             if part[:3].upper() in ("OU=", "CN=")]
    return "/".join(reversed(names))


# --------------------------------------------------------------- файл
def from_file(path):
    """
    Список из текстового файла: строка на компьютер, «имя» или «имя адрес».
    Пустые строки и строки с # пропускаются.
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise DirectoryError("Не прочитать список компьютеров: %s" % exc)
    computers = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            fields = re.split(r"[\s,;]+", line)
            computers.append(Computer(name=fields[0], host=fields[1] if len(fields) > 1 else ""))
    return computers


def describe_source(computers_file="", base=""):
    if computers_file:
        return "файл %s" % computers_file
    return "Active Directory" + (" (%s)" % base if base else "")


def loader(computers_file="", base=""):
    """Функция без аргументов, которая каждый раз заново читает список."""
    if computers_file:
        return lambda: from_file(computers_file)
    if sys.platform != "win32":
        def unavailable():
            raise DirectoryError(
                "Active Directory читается только на Windows-машине в домене. "
                "Здесь укажите список компьютеров файлом: --computers ФАЙЛ")
        return unavailable
    return lambda: from_active_directory(base)

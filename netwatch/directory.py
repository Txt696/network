"""
Откуда берётся список компьютеров: Active Directory или простой файл.

AD читается через PowerShell и ADSI ([adsisearcher]) — это есть в любой
Windows, вошедшей в домен, без RSAT и без модуля ActiveDirectory. Права
нужны обычные: объекты компьютеров может читать любая доменная учётная
запись, в том числе SYSTEM на машине в домене (в AD она ходит как
учётная запись самого компьютера).
"""

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from netwatch import powershell

FILETIME_EPOCH = 11644473600  # секунд между 1601-01-01 и 1970-01-01
FILETIME_NEVER = 0x7FFFFFFFFFFFFFFF
ACCOUNTDISABLE = 0x2  # бит userAccountControl «учётная запись отключена»
SERVER_TRUST_ACCOUNT = 0x2000  # бит userAccountControl «контроллер домена»
AD_TIMEOUT = 180

AD_FIELDS = ("name", "dnshostname", "operatingsystem", "useraccountcontrol",
             "lastlogontimestamp", "pwdlastset", "distinguishedname", "description")

AD_SCRIPT = r"""
  $searcher = [adsisearcher]'(objectCategory=computer)'
  $base = __BASE__
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
"""

NO_POWERSHELL = ("PowerShell не найден. Список из Active Directory читается на Windows-машине "
                 "в домене; на этой системе укажите список компьютеров файлом: --computers ФАЙЛ")


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
    is_dc: bool = False                   # контроллер домена — с него собираются журналы

    def __post_init__(self):
        self.host = self.host or self.name


# ----------------------------------------------------------------- AD
def from_active_directory(base="", run=subprocess.run):
    """Компьютеры из AD. base — DN подразделения, если нужен не весь домен."""
    body = (AD_SCRIPT.replace("__BASE__", powershell.quote(base))
            .replace("__FIELDS__", ",".join(AD_FIELDS)))
    try:
        rows = powershell.run(body, AD_TIMEOUT, run=run)
    except powershell.NoPowerShell:
        raise DirectoryError(NO_POWERSHELL)
    except powershell.PowerShellError as exc:
        raise DirectoryError("Ошибка запроса к AD: %s" % exc)
    return computers_from_rows(rows)


def parse_ad_output(stdout):
    try:
        rows = powershell.unpack(stdout)
    except powershell.PowerShellError as exc:
        raise DirectoryError("Ошибка запроса к AD: %s" % exc)
    return computers_from_rows(rows)


def computers_from_rows(rows):
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
        is_dc=bool(flags & SERVER_TRUST_ACCOUNT),
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

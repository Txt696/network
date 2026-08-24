# NetVault + NetMaster

Два приложения для сетевого инженера, работающие с одним хранилищем данных.

- **NetVault** — где всё записано: серверы, свитчи, роутеры, их адреса,
  модели, стойки, VLAN'ы, заметки и пароли. «Обсидиан + менеджер паролей»
  в одном.
- **NetMaster** — чем всё это управляется: подключение по SSH, терминал,
  массовый прогон команд, пинг и сетевые утилиты. Своей базы устройств
  у него нет — он берёт данные и доступы из хранилища NetVault.

Смысл связки: адрес и пароль вводятся один раз, в NetVault. Дальше
«какой был адрес у core-sw-01» и «под каким логином туда заходить»
больше не вопрос — NetMaster подставляет это сам.

## Как устроено хранилище

Обычная папка на диске, которую видно в проводнике и можно синхронизировать
любым способом (git, облако, флешка):

```
NetVault/
  devices/       по одному .md на устройство: YAML-фронтматтер + свободный текст
  notes/         произвольные заметки и собранные с устройств конфиги
  templates/     шаблоны заметок
  secrets.enc    логины и пароли, AES-256-GCM под мастер-паролем
  .netvault/     настройки и журнал изменений
```

Папку `devices/` можно открыть как vault в Obsidian и писать в заметках
что угодно — приложение читает только фронтматтер, остальной текст не трогает.
Открытый текст — это данные об оборудовании; пароли в заметки не попадают
никогда, они лежат отдельно в `secrets.enc`.

Заметка устройства выглядит так:

```markdown
---
name: core-sw-01
kind: switch
mgmt_ip: 10.0.0.1
vendor: cisco
site: DC1
protocol: ssh
port: 22
tags:
  - core
---

Аплинк в ядро, порт Gi1/0/48. Меняли БП в марте.
```

## Шифрование доступов

```
мастер-пароль --Argon2id (или scrypt)--> KEK
KEK --AES-256-GCM--> DEK
DEK --AES-256-GCM--> все секреты
```

Двухуровневая схема нужна, чтобы смена мастер-пароля перешифровывала только
ключ, а не весь файл. Мастер-пароль нигде не хранится и не восстанавливается —
забыл пароль, потерял секреты (инвентарь в `devices/` при этом остаётся).
Хранилище автоматически закрывается после простоя, буфер обмена чистится.

## Установка

```bash
pip install cryptography paramiko argon2-cffi
```

`argon2-cffi` не обязателен: без него используется scrypt. Для графики нужен
tkinter — на Windows он ставится галочкой «tcl/tk and IDLE» в установщике
Python, на Linux `sudo apt install python3-tk`.

## Запуск

```bash
python netvault/main.py     # хранилище: карточки устройств, поиск, пароли
python netmaster/main.py    # управление: терминал, массовые команды, утилиты
```

Оба приложения помнят последнее открытое хранилище (`~/.netvault/app.json`),
путь можно задать явно: `--vault ПУТЬ` или переменной `NETVAULT_PATH`.

## Консоль

То же самое без графики — удобно для скриптов и по SSH на прыжковом хосте.

```bash
# хранилище
python netvault/cli.py init
python netvault/cli.py add --name core-sw-01 --kind switch --ip 10.0.0.1 \
                           --vendor cisco --site DC1 --tags core,dc1
python netvault/cli.py set-secret core-sw-01 --username admin --password ...
python netvault/cli.py search "10.0.0."
python netvault/cli.py show core-sw-01
python netvault/cli.py export --format csv        # инвентарь без паролей
python netvault/cli.py doctor                     # проверка на ошибки

# управление
python netmaster/cli.py targets --tag core                  # кто попадёт под фильтр
python netmaster/cli.py ping --site DC1
python netmaster/cli.py run --tag core "show version" --save # вывод ляжет в notes/
python netmaster/cli.py run --device core-sw-01 "show run" --enable
```

Мастер-пароль спрашивается интерактивно; для автоматизации его можно передать
переменной `NETVAULT_PASSWORD`.

## Тесты

```bash
python -m unittest discover -s tests
```

## Структура кода

```
netcore/     общее ядро: модель устройства, Markdown-заметки, шифрование
netvault/    приложение-хранилище (GUI + CLI)
netmaster/   приложение-управление (GUI + CLI), читает хранилище через netcore
```

Ядро не зависит от GUI, поэтому всё, что умеют приложения, доступно и из
консоли, и из своих скриптов: `from netcore import Vault`.

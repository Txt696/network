# NetMaster

Управление сетевым оборудованием по данным из хранилища NetVault.
Своей базы подключений у NetMaster нет: список устройств он читает из
Markdown-заметок хранилища, логины и пароли — из зашифрованного `secrets.enc`.

Общее описание связки и установка — в [README репозитория](../README.md).

## Что умеет

- дерево устройств из хранилища с поиском и фильтрами (тип, площадка, тег);
- терминал по SSH во вкладках, подключение без ввода пароля — он берётся
  из хранилища;
- массовый прогон команд на выбранных устройствах в несколько потоков,
  с отключением постраничного вывода по вендору
  (Cisco, Huawei, HP, Juniper, Arista, Extreme, MikroTik);
- сохранение вывода команд обратно в хранилище, рядом с заметкой устройства;
- сетевые утилиты: ping, traceroute, сканирование портов, DNS, калькулятор
  подсетей.

## Запуск

```bash
python netmaster/main.py                 # графический режим
python netmaster/main.py --device core-sw-01   # сразу подключиться

python netmaster/cli.py targets --tag core
python netmaster/cli.py ping --site DC1
python netmaster/cli.py run --tag core "show version" --save
```

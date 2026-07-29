# NetMaster - Professional Network Engineer Tool

Привет, Али! Это твой новый инструмент для работы с сетями.

## Описание

NetMaster - кроссплатформенное приложение (Windows/Linux) для сетевых инженеров, 
вдохновленное MobaXterm, но оптимизированное под твои нужды.

## Возможности

### Реализовано (v0.2):
- ✅ Графический интерфейс на Python/Tkinter
- ✅ Менеджер соединений (SSH, Telnet, Serial)
- ✅ Сохранение конфигурации в локальный JSON файл
- ✅ Вкладки для нескольких сессий
- ✅ Дерево сохраненных подключений
- ✅ Быстрое подключение
- ✅ Контекстные меню
- ✅ Темная тема терминала
- ✅ SSH клиент на базе Paramiko
- ✅ Сетевые утилиты: Ping, Traceroute
- ✅ Сканирование портов
- ✅ Калькулятор подсетей
- ✅ DNS lookup

### В разработке:
- 🔄 Интеграция GUI с сетевыми утилитами
- 🔄 Макросы и скрипты
- 🔄 SFTP браузер файлов
- 🔄 Экспорт конфигураций
- 🴁 Поддержка Serial подключений

## Установка зависимостей

```bash
pip install paramiko pynacl
```

Для сетевых утилит убедитесь, что установлены системные пакеты:
- Linux: `apt-get install iputils-ping traceroute net-tools`
- Windows: Встроено в систему

## Запуск приложения

```bash
cd /workspace/netmaster
python3 main.py
```

**Примечание:** Для работы GUI требуется X11 сервер. 
- В Linux с графической средой - запускается напрямую
- В Windows - используйте WSL с Xming/VcXsrv или запустите нативный Python
- Без графической среды - тестируйте модули через консоль:

```bash
python3 -c "from core.network_tools import ping; print(ping('8.8.8.8'))"
```

## Структура проекта

```
netmaster/
├── main.py                 # Точка входа
├── core/                   # Ядро приложения
│   ├── ssh_client.py       # SSH клиент (Paramiko)
│   └── network_tools.py    # Сетевые утилиты
├── gui/                    # Графический интерфейс
│   ├── main_window.py      # Главное окно
│   ├── terminal_widget.py  # Виджет терминала
│   └── connection_dialog.py # Диалог подключений
└── utils/                  # Утилиты
    └── config.py           # Управление конфигами
```

## Хранение данных

Конфигурация сохраняется в:
- Linux: `~/.netmaster/connections.json`
- Windows: `%USERPROFILE%\.netmaster\connections.json`

## Примеры использования модулей

### Ping
```python
from core.network_tools import ping
result = ping('192.168.1.1', count=4)
print(result['stats'])  # {'min': 1, 'avg': 2, 'max': 3, 'loss': 0}
```

### Калькулятор подсетей
```python
from core.network_tools import calc_subnet
info = calc_subnet('10.0.0.0/16')
print(f"Usable hosts: {info['usable_hosts']}")  # 65534
```

### Сканирование портов
```python
from core.network_tools import scan_ports
result = scan_ports('192.168.1.1', [22, 80, 443])
print(f"Open ports: {result['results']['open']}")
```

### SSH подключение
```python
from core.ssh_client import SSHClient
ssh = SSHClient()
success, msg = ssh.connect('192.168.1.1', username='admin', password='pass')
ssh.send_command('show version')
ssh.disconnect()
```

## Вопросы для улучшения, Али

1. Какие протоколы кроме SSH/Telnet тебе нужны? (Serial, FTP, SFTP, RDP, VNC?)
2. Нужна ли синхронизация между устройствами?
3. Какие сетевые утилиты наиболее важны? (SNMP, CDP/LLDP parser, ARP table?)
4. Нужна ли поддержка сессий с разными цветами/тегами?
5. Интеграция с системами мониторинга (PRTG, Zabbix)?
6. Нужен ли экспорт отчетов в PDF/HTML?
7. Макросы для автоматизации рутинных команд?

## Следующие шаги

1. Интеграция сетевых утилит в GUI
2. Добавление реального SSH подключения в терминал
3. Создание инструментов для работы с конфигурациями Cisco/Juniper
4. Добавление поддержки Serial подключений (pyserial)

Автор: AI Assistant
Специально для Али

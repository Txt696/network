"""
Выполнение команд на устройствах по SSH — по одному и массово.

Работа идёт через интерактивную сессию (invoke_shell), потому что
свитчи и роутеры часто не поддерживают одиночный exec-канал.
Вывод читается до тишины на канале, поэтому подходит и для Linux-серверов,
и для Cisco/Huawei/MikroTik.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Команды отключения постраничного вывода по вендорам.
PAGER_OFF = {
    "cisco": ["terminal length 0"],
    "hp": ["screen-length disable"],
    "huawei": ["screen-length 0 temporary"],
    "juniper": ["set cli screen-length 0"],
    "mikrotik": [],
    "arista": ["terminal length 0"],
    "extreme": ["disable clipaging"],
}


class CommandResult:
    """Результат выполнения набора команд на одном устройстве."""

    def __init__(self, target, commands):
        self.device_id = target.id
        self.name = target.name
        self.host = target.host
        self.commands = list(commands)
        self.output = ""
        self.error = ""
        self.ok = False
        self.duration = 0.0

    def as_text(self):
        header = "# %s (%s)\n\nКоманды: %s\n\n" % (
            self.name, self.host or "-", ", ".join(self.commands))
        if self.error:
            header += "Ошибка: %s\n\n" % self.error
        return header + "```\n" + self.output.strip() + "\n```\n"

    def __repr__(self):
        return "<CommandResult %s ok=%s>" % (self.device_id, self.ok)


def pager_commands(vendor):
    vendor = (vendor or "").lower()
    for key, commands in PAGER_OFF.items():
        if key in vendor:
            return commands
    return []


def _read_until_idle(channel, idle=0.7, timeout=30.0):
    """Читать канал, пока данные не перестанут приходить."""
    chunks = []
    last_data = time.time()
    started = time.time()
    while time.time() - started < timeout:
        if channel.recv_ready():
            data = channel.recv(65535).decode("utf-8", errors="replace")
            if data:
                chunks.append(data)
                last_data = time.time()
                continue
        if channel.closed or channel.exit_status_ready():
            break
        if chunks and time.time() - last_data > idle:
            break
        time.sleep(0.05)
    return "".join(chunks)


class ParamikoSession:
    """Интерактивная SSH-сессия к одному устройству."""

    def __init__(self, target, timeout=15):
        self.target = target
        self.timeout = timeout
        self.client = None
        self.channel = None

    def open(self):
        import paramiko  # импорт здесь, чтобы модуль читался и без paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = {
            "hostname": self.target.host,
            "port": self.target.port,
            "username": self.target.username,
            "timeout": self.timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.target.key_file:
            kwargs["key_filename"] = self.target.key_file
            if self.target.key_passphrase:
                kwargs["passphrase"] = self.target.key_passphrase
            if self.target.password:
                kwargs["password"] = self.target.password
        else:
            kwargs["password"] = self.target.password
        client.connect(**kwargs)
        channel = client.invoke_shell(term="vt100", width=200, height=1000)
        channel.settimeout(self.timeout)
        self.client = client
        self.channel = channel
        _read_until_idle(channel, idle=0.5, timeout=5)
        return self

    def send(self, command, idle=0.7, timeout=30.0):
        self.channel.send(command + "\n")
        return _read_until_idle(self.channel, idle=idle, timeout=timeout)

    def enable(self, password):
        """Перейти в привилегированный режим (Cisco-подобные устройства)."""
        output = self.send("enable", idle=0.5, timeout=10)
        if "assword" in output:
            output += self.send(password, idle=0.5, timeout=10)
        return output

    def close(self):
        for resource in (self.channel, self.client):
            try:
                if resource:
                    resource.close()
            except Exception:
                pass
        self.channel = None
        self.client = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc_info):
        self.close()


def run_on_device(target, commands, timeout=30, use_enable=False,
                  disable_pager=True, session_factory=ParamikoSession):
    """Выполнить команды на одном устройстве. Исключения не пробрасываются."""
    result = CommandResult(target, commands)
    started = time.time()
    if not target.host:
        result.error = "не задан адрес устройства"
        return result
    if not target.has_credentials:
        result.error = "нет сохранённых доступов в хранилище"
        return result

    session = session_factory(target, timeout=timeout)
    try:
        session.open()
        buffer = []
        if use_enable and target.enable_password:
            buffer.append(session.enable(target.enable_password))
        if disable_pager:
            for command in pager_commands(target.vendor):
                buffer.append(session.send(command, idle=0.4, timeout=10))
        for command in commands:
            buffer.append(session.send(command, timeout=timeout))
        result.output = "".join(buffer)
        result.ok = True
    except Exception as exc:  # сеть, авторизация, таймаут — показываем как есть
        result.error = "%s: %s" % (type(exc).__name__, exc)
    finally:
        session.close()
        result.duration = round(time.time() - started, 1)
    return result


def run_on_many(targets, commands, workers=8, progress=None, stop_event=None, **kwargs):
    """Выполнить команды на нескольких устройствах параллельно.

    progress(done, total, result) вызывается после каждого устройства.
    """
    targets = list(targets)
    results = []
    lock = threading.Lock()
    total = len(targets)
    if not total:
        return results

    def worker(target):
        if stop_event is not None and stop_event.is_set():
            result = CommandResult(target, commands)
            result.error = "отменено"
            return result
        return run_on_device(target, commands, **kwargs)

    with ThreadPoolExecutor(max_workers=max(1, min(workers, total))) as pool:
        for result in pool.map(worker, targets):
            with lock:
                results.append(result)
                if progress:
                    progress(len(results), total, result)
    return results


def save_results(inventory, results, title="command-output"):
    """Сохранить вывод в хранилище (notes/collected/<устройство>/)."""
    saved = []
    for result in results:
        if result.output.strip():
            saved.append(inventory.save_output(result.device_id, title, result.as_text()))
    return saved


def summarize(results):
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    lines = ["Успешно: %d, с ошибкой: %d" % (len(ok), len(failed))]
    for result in failed:
        lines.append("  %s (%s): %s" % (result.name, result.host or "-", result.error))
    return "\n".join(lines)

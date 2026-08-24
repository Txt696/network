"""
Файлы на устройстве по SFTP: забрать конфиг, положить прошивку.

Подключение то же самое, что у терминала (`runner.connect_client`),
поэтому логин и пароль снова берутся из хранилища, а не спрашиваются.
Пути на устройстве всегда в стиле POSIX, даже когда NetMaster
запущен на Windows.
"""

import os
import posixpath
from datetime import datetime

UNITS = ("Б", "КБ", "МБ", "ГБ", "ТБ")


def human_size(size):
    """Размер файла человеческими буквами."""
    try:
        size = float(size)
    except (TypeError, ValueError):
        return ""
    for unit in UNITS:
        if size < 1024 or unit == UNITS[-1]:
            return "%d %s" % (size, unit) if unit == UNITS[0] else "%.1f %s" % (size, unit)
        size /= 1024
    return ""


def human_time(stamp):
    try:
        return datetime.fromtimestamp(stamp).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def join(path, name):
    """Путь на устройстве: всегда через прямые слэши."""
    return posixpath.normpath(posixpath.join(path or "/", name))


def parent(path):
    """Родительская папка; выше корня не поднимаемся."""
    return posixpath.dirname(posixpath.normpath(path or "/")) or "/"


class Entry:
    """Строка списка файлов."""

    def __init__(self, name, size=0, mtime=0, is_dir=False, mode=""):
        self.name = name
        self.size = size
        self.mtime = mtime
        self.is_dir = is_dir
        self.mode = mode

    @property
    def size_text(self):
        return "<папка>" if self.is_dir else human_size(self.size)

    @property
    def time_text(self):
        return human_time(self.mtime)

    def __repr__(self):
        return "<Entry %s%s>" % (self.name, "/" if self.is_dir else "")


def sort_entries(entries):
    """Папки сверху, дальше по имени — как в любом файловом менеджере."""
    return sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))


def from_attr(attr):
    """Entry из paramiko.SFTPAttributes."""
    import stat

    mode = getattr(attr, "st_mode", 0) or 0
    return Entry(name=attr.filename,
                 size=getattr(attr, "st_size", 0) or 0,
                 mtime=getattr(attr, "st_mtime", 0) or 0,
                 is_dir=stat.S_ISDIR(mode),
                 mode=stat.filemode(mode) if mode else "")


class SftpSession:
    """Файловый доступ к одному устройству."""

    def __init__(self, target, timeout=15):
        self.target = target
        self.timeout = timeout
        self.client = None
        self.sftp = None

    def open(self):
        from netmaster.core.runner import connect_client

        self.client = connect_client(self.target, self.timeout)
        self.sftp = self.client.open_sftp()
        return self

    @property
    def is_open(self):
        return self.sftp is not None

    def home(self):
        """Домашняя папка пользователя — с неё начинаем просмотр."""
        try:
            return self.sftp.normalize(".")
        except Exception:
            return "/"

    def listdir(self, path):
        """Содержимое папки: (нормализованный путь, строки списка)."""
        path = self.sftp.normalize(path or ".")
        entries = [from_attr(attr) for attr in self.sftp.listdir_attr(path)]
        return path, sort_entries(entries)

    def download(self, remote, local_dir, progress=None):
        """Забрать файл с устройства в папку на своём компьютере."""
        local = os.path.join(local_dir, posixpath.basename(remote))
        self.sftp.get(remote, local, callback=progress)
        return local

    def upload(self, local, remote_dir, progress=None):
        """Положить файл со своего компьютера на устройство."""
        remote = join(remote_dir, os.path.basename(local))
        self.sftp.put(local, remote, callback=progress)
        return remote

    def mkdir(self, path):
        self.sftp.mkdir(path)
        return path

    def rename(self, old, new):
        self.sftp.rename(old, new)
        return new

    def delete(self, entry_path, is_dir=False):
        if is_dir:
            self.sftp.rmdir(entry_path)
        else:
            self.sftp.remove(entry_path)
        return entry_path

    def close(self):
        for resource in (self.sftp, self.client):
            try:
                if resource:
                    resource.close()
            except Exception:
                pass
        self.sftp = None
        self.client = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc_info):
        self.close()

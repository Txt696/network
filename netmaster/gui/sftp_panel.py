"""
Панель файлов на устройстве (SFTP) — как файловая панель в MobaXterm.

Открывается отдельной вкладкой рядом с терминалом, подключается теми же
логином и паролем из хранилища. Все обращения к сети идут в отдельном
потоке, чтобы окно не подвисало на больших файлах.
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from netmaster.core import sftp

APP_TITLE = "NetMaster"


class SftpPanel(ttk.Frame):
    """Список файлов на устройстве с загрузкой и выгрузкой."""

    def __init__(self, parent, target, session=None):
        super().__init__(parent)
        self.target = target
        self.session = session or sftp.SftpSession(target)
        self.path = "/"
        self.entries = []
        self.busy = False

        self._build_ui()
        self.open()

    # ------------------------------------------------------------ интерфейс
    def _build_ui(self):
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(top, text="↑ Вверх", command=self.go_up).pack(side=tk.LEFT)
        ttk.Button(top, text="Обновить", command=self.refresh).pack(side=tk.LEFT, padx=4)
        ttk.Label(top, text="Папка:").pack(side=tk.LEFT, padx=(8, 2))
        self.path_var = tk.StringVar(value=self.path)
        path_entry = ttk.Entry(top, textvariable=self.path_var)
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        path_entry.bind("<Return>", lambda e: self.list_dir(self.path_var.get()))
        ttk.Button(top, text="Перейти",
                   command=lambda: self.list_dir(self.path_var.get())).pack(side=tk.LEFT)

        columns = ("size", "time", "mode")
        self.tree = ttk.Treeview(self, columns=columns, show="tree headings",
                                 selectmode="extended")
        self.tree.heading("#0", text="Имя")
        self.tree.heading("size", text="Размер")
        self.tree.heading("time", text="Изменён")
        self.tree.heading("mode", text="Права")
        self.tree.column("#0", width=340)
        self.tree.column("size", width=90, anchor=tk.E, stretch=False)
        self.tree.column("time", width=130, stretch=False)
        self.tree.column("mode", width=110, stretch=False)
        scroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(4, 0), pady=2)
        scroll.grid(row=1, column=1, sticky="ns", pady=2)
        self.tree.bind("<Double-1>", self._on_open)
        self.tree.bind("<Return>", self._on_open)
        self.tree.bind("<Delete>", lambda e: self.delete_selected())

        buttons = ttk.Frame(self)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Button(buttons, text="Скачать", command=self.download_selected).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Загрузить…", command=self.upload).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Новая папка", command=self.make_dir).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Переименовать",
                   command=self.rename_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Удалить", command=self.delete_selected).pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(buttons, mode="determinate", length=220)
        self.progress.pack(side=tk.RIGHT)

        self.status = ttk.Label(self, text="", anchor=tk.W)
        self.status.grid(row=3, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 4))

    # ------------------------------------------------------------ служебное
    def _say(self, text):
        self.status.config(text=text)

    def _run(self, work, done=None, what="операция"):
        """Сетевую работу — в поток, результат вернуть в интерфейс."""
        if self.busy:
            self._say("Дождитесь окончания предыдущей операции")
            return
        self.busy = True

        def worker():
            try:
                result = work()
            except Exception as exc:
                # exc живёт только внутри except — передаём его в лямбду значением,
                # иначе обработчик упадёт и панель останется «занятой» навсегда.
                self.after(0, lambda exc=exc: self._failed(what, exc))
                return
            self.after(0, lambda: self._finished(result, done))

        threading.Thread(target=worker, daemon=True).start()

    def _finished(self, result, done):
        self.busy = False
        if done:
            done(result)

    def _failed(self, what, exc):
        self.busy = False
        self._say("Не удалось: %s — %s" % (what, exc))

    # ------------------------------------------------------------ навигация
    def open(self):
        self._say("Подключение к %s …" % (self.target.host or "?"))

        def work():
            if not self.session.is_open:
                self.session.open()
            return self.session.home()

        self._run(work, done=self.list_dir, what="подключение")

    def list_dir(self, path):
        self._say("Читаю %s …" % path)
        self._run(lambda: self.session.listdir(path), done=self._show, what="чтение папки")

    def _show(self, listing):
        self.path, self.entries = listing
        self.path_var.set(self.path)
        self.tree.delete(*self.tree.get_children())
        for index, entry in enumerate(self.entries):
            self.tree.insert("", tk.END, iid=str(index),
                             text=("📁 " if entry.is_dir else "📄 ") + entry.name,
                             values=(entry.size_text, entry.time_text, entry.mode))
        self._say("%s — объектов: %d" % (self.path, len(self.entries)))

    def refresh(self):
        self.list_dir(self.path)

    def go_up(self):
        self.list_dir(sftp.parent(self.path))

    def selected(self):
        return [self.entries[int(iid)] for iid in self.tree.selection()
                if iid.isdigit() and int(iid) < len(self.entries)]

    def _on_open(self, _event=None):
        chosen = self.selected()
        if not chosen:
            return
        entry = chosen[0]
        if entry.is_dir:
            self.list_dir(sftp.join(self.path, entry.name))
        else:
            self.download_selected()

    # ------------------------------------------------------------ действия
    def _progress(self, done, total):
        self.after(0, lambda: self.progress.config(
            maximum=max(total, 1), value=done))

    def download_selected(self):
        files = [e for e in self.selected() if not e.is_dir]
        if not files:
            self._say("Выберите файлы для скачивания")
            return
        local_dir = filedialog.askdirectory(title="Куда сохранить", parent=self)
        if not local_dir:
            return

        def work():
            saved = []
            for entry in files:
                saved.append(self.session.download(sftp.join(self.path, entry.name),
                                                   local_dir, progress=self._progress))
            return saved

        self._say("Скачиваю файлов: %d …" % len(files))
        self._run(work, done=lambda saved: self._say("Сохранено: %s" % ", ".join(saved)),
                  what="скачивание")

    def upload(self):
        paths = filedialog.askopenfilenames(title="Что загрузить на устройство", parent=self)
        if not paths:
            return

        def work():
            for local in paths:
                self.session.upload(local, self.path, progress=self._progress)
            return len(paths)

        self._say("Загружаю файлов: %d …" % len(paths))
        self._run(work, done=lambda count: self._after_change("Загружено файлов: %d" % count),
                  what="загрузка")

    def make_dir(self):
        name = _ask(self, "Новая папка", "Имя папки:")
        if not name:
            return
        self._run(lambda: self.session.mkdir(sftp.join(self.path, name)),
                  done=lambda _r: self._after_change("Папка создана: %s" % name),
                  what="создание папки")

    def rename_selected(self):
        chosen = self.selected()
        if not chosen:
            self._say("Выберите файл или папку")
            return
        entry = chosen[0]
        name = _ask(self, "Переименовать", "Новое имя:", entry.name)
        if not name or name == entry.name:
            return
        self._run(lambda: self.session.rename(sftp.join(self.path, entry.name),
                                              sftp.join(self.path, name)),
                  done=lambda _r: self._after_change("Переименовано в %s" % name),
                  what="переименование")

    def delete_selected(self):
        chosen = self.selected()
        if not chosen:
            self._say("Выберите, что удалить")
            return
        names = ", ".join(e.name for e in chosen)
        if not messagebox.askyesno(APP_TITLE, "Удалить с устройства: %s?" % names, parent=self):
            return

        def work():
            for entry in chosen:
                self.session.delete(sftp.join(self.path, entry.name), entry.is_dir)
            return len(chosen)

        self._run(work, done=lambda count: self._after_change("Удалено: %d" % count),
                  what="удаление")

    def _after_change(self, message):
        self._say(message)
        self.progress.config(value=0)
        self.refresh()

    def close(self):
        self.session.close()


def _ask(parent, title, prompt, initial=""):
    """Маленький диалог ввода строки (без tkinter.simpledialog с его видом)."""
    window = tk.Toplevel(parent)
    window.title(title)
    window.transient(parent.winfo_toplevel())
    window.resizable(False, False)
    value = tk.StringVar(value=initial)
    ttk.Label(window, text=prompt, padding=(10, 8, 10, 2)).pack(anchor=tk.W)
    entry = ttk.Entry(window, textvariable=value, width=40)
    entry.pack(padx=10, pady=4)
    entry.focus_set()
    entry.select_range(0, tk.END)
    result = {}

    def ok(_event=None):
        result["value"] = value.get().strip()
        window.destroy()

    buttons = ttk.Frame(window, padding=(10, 4, 10, 10))
    buttons.pack(fill=tk.X)
    ttk.Button(buttons, text="OK", command=ok).pack(side=tk.RIGHT)
    ttk.Button(buttons, text="Отмена", command=window.destroy).pack(side=tk.RIGHT, padx=4)
    entry.bind("<Return>", ok)
    window.bind("<Escape>", lambda e: window.destroy())
    window.grab_set()
    parent.wait_window(window)
    return result.get("value", "")


def local_name(path):
    """Имя файла на своём компьютере — для подписей в интерфейсе."""
    return os.path.basename(path)

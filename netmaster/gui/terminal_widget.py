"""
Виджет терминала: интерактивная SSH-сессия к устройству из хранилища.

Логин и пароль берутся из NetVault, руками ничего вводить не нужно.
Печатать можно прямо в чёрной области, как в PuTTY: символы уходят
в канал, а на экране появляется эхо от самого устройства. Поле «>»
внизу оставлено для длинных команд и истории по стрелкам.
"""

import queue
import threading
import tkinter as tk
from tkinter import ttk

from netmaster.core import ansi

MAX_LINES = 5000  # сколько строк держим в буфере вывода
MAX_MACRO_BUTTONS = 8  # больше на панели просто не помещается


class TerminalWidget(ttk.Frame):
    """Одна вкладка-сессия."""

    def __init__(self, parent, target=None, on_state_change=None,
                 macros=None, on_edit_macros=None):
        super().__init__(parent)
        self.target = target
        self.on_state_change = on_state_change
        self.on_edit_macros = on_edit_macros
        self.macros = list(macros or [])
        self.client = None
        self.connected = False
        self.history = []
        self.history_index = 0
        self._queue = queue.Queue()
        self._line = ""  # незавершённая последняя строка вывода

        self._build_ui()
        self._poll_output()

    # ------------------------------------------------------------ интерфейс
    def _build_ui(self):
        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)

        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", padx=2, pady=2)
        self.status_label = ttk.Label(bar, text="Не подключено", style="Disconnected.TLabel")
        self.status_label.pack(side=tk.LEFT)
        ttk.Button(bar, text="Отключить", command=self.disconnect).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bar, text="Переподключить", command=self.connect).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bar, text="Очистить", command=self.clear).pack(side=tk.RIGHT, padx=2)

        self.macro_bar = ttk.Frame(self)
        self.macro_bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=2)
        self._build_macro_bar()

        self.output = tk.Text(self, bg="#1e1e1e", fg="#d4d4d4", insertbackground="#ffffff",
                              font=("Consolas", 10), wrap=tk.NONE, undo=False, takefocus=True)
        scroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set, state=tk.DISABLED)
        self.output.grid(row=2, column=0, sticky="nsew", padx=(2, 0), pady=2)
        scroll.grid(row=2, column=1, sticky="ns", pady=2)
        self.output.tag_configure("system", foreground="#569cd6")
        self.output.tag_configure("error", foreground="#f48771")

        # Ввод прямо в области вывода: символы уходят на устройство,
        # локально ничего не вставляем — эхо придёт от него же.
        self.output.bind("<Key>", self._on_key)
        self.output.bind("<Button-1>", lambda e: self.output.focus_set())
        self.output.bind("<Button-3>", self._paste)

        entry_frame = ttk.Frame(self)
        entry_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
        ttk.Label(entry_frame, text=">").pack(side=tk.LEFT)
        self.command_entry = ttk.Entry(entry_frame, font=("Consolas", 10))
        self.command_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self.command_entry.bind("<Return>", self._send_command)
        self.command_entry.bind("<Up>", self._history_prev)
        self.command_entry.bind("<Down>", self._history_next)
        self.command_entry.bind("<Control-c>", lambda e: self._send_raw("\x03"))
        ttk.Button(entry_frame, text="Отправить", command=self._send_command).pack(side=tk.LEFT)

    def _build_macro_bar(self):
        """Кнопки сохранённых наборов команд для этого устройства."""
        for child in self.macro_bar.winfo_children():
            child.destroy()
        if not (self.macros or self.on_edit_macros):
            return
        ttk.Label(self.macro_bar, text="Команды:").pack(side=tk.LEFT, padx=(0, 4))
        for item in self.macros[:MAX_MACRO_BUTTONS]:
            ttk.Button(self.macro_bar, text=item["name"], width=max(10, len(item["name"]) + 2),
                       command=lambda m=item: self.send_macro(m)).pack(side=tk.LEFT, padx=2)
        if self.on_edit_macros:
            ttk.Button(self.macro_bar, text="…", width=3,
                       command=self.on_edit_macros).pack(side=tk.LEFT, padx=2)

    def set_macros(self, macros):
        """Обновить панель после правки списка команд."""
        self.macros = list(macros or [])
        self._build_macro_bar()

    def focus_terminal(self):
        self.output.focus_set()

    # ------------------------------------------------------------ соединение
    def connect(self):
        """Подключиться к устройству в фоновом потоке."""
        if self.connected or not self.target:
            return
        if not self.target.host:
            self._write("У устройства не указан адрес.\n", "error")
            return
        self._write("Подключение к %s@%s:%s …\n" % (
            self.target.username or "?", self.target.host, self.target.port), "system")
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self):
        from netmaster.core.runner import ParamikoSession

        session = ParamikoSession(self.target)
        try:
            session.open()
        except Exception as exc:
            self._queue.put(("error", "Не удалось подключиться: %s\n" % exc))
            self._queue.put(("state", False))
            return
        self.client = session
        self._queue.put(("state", True))
        self._queue.put(("system", "Соединение установлено.\n"))
        channel = session.channel
        while True:
            try:
                if channel.closed:
                    break
                if channel.recv_ready():
                    data = channel.recv(65535).decode("utf-8", errors="replace")
                    if not data:
                        break
                    self._queue.put(("output", data))
                else:
                    threading.Event().wait(0.05)
            except Exception:
                break
        self._queue.put(("system", "\nСоединение закрыто.\n"))
        self._queue.put(("state", False))

    def disconnect(self):
        if self.client:
            self.client.close()
            self.client = None
        self.connected = False
        self._set_state(False)

    def _set_state(self, connected):
        self.connected = connected
        self.status_label.config(
            text="Подключено: %s" % self.target.describe() if connected and self.target
            else "Не подключено",
            style="Connected.TLabel" if connected else "Disconnected.TLabel")
        if connected:
            self.focus_terminal()
        if self.on_state_change:
            self.on_state_change(self, connected)

    # ---------------------------------------------------------------- ввод
    def _on_key(self, event):
        """Клавиша нажата в области вывода — отправить её на устройство."""
        if event.state & 0x20008:  # Alt — оставляем системе (меню, Alt+F4)
            return None
        if event.state & 0x4:  # Control
            return self._on_control_key(event)
        data = ansi.KEYS.get(event.keysym)
        if data is None:
            data = event.char
        if data:
            self._send_raw(data)
        return "break"

    def _on_control_key(self, event):
        key = event.keysym.lower()
        if key == "c" and self.output.tag_ranges("sel"):
            self._copy_selection()  # есть выделение — копируем, как в PuTTY
            return "break"
        if key in ("v", "insert"):
            return self._paste()
        code = ansi.ctrl_code(key)
        if code:
            self._send_raw(code)
        return "break"

    def _copy_selection(self):
        try:
            text = self.output.get("sel.first", "sel.last")
        except tk.TclError:
            return
        self.clipboard_clear()
        self.clipboard_append(text)

    def _paste(self, _event=None):
        try:
            data = self.clipboard_get()
        except tk.TclError:
            return "break"
        self.output.focus_set()
        self._send_raw(data.replace("\r\n", "\r").replace("\n", "\r"))
        return "break"

    def _send_command(self, _event=None):
        command = self.command_entry.get()
        self.command_entry.delete(0, tk.END)
        if command:
            self.history.append(command)
            self.history_index = len(self.history)
        self._send_raw(command + "\n")

    def _send_raw(self, data):
        if not (self.connected and self.client and self.client.channel):
            self._write("Нет активного подключения.\n", "error")
            return
        try:
            self.client.channel.send(data)
        except Exception as exc:
            self._write("Ошибка отправки: %s\n" % exc, "error")

    def send_line(self, command):
        """Отправить команду программно (из макросов и меню)."""
        self._send_raw(command + "\n")

    def send_macro(self, item):
        """Отправить все команды набора по очереди."""
        for command in item.get("commands", []):
            self.send_line(command)
        self.focus_terminal()

    def _history_prev(self, _event=None):
        if self.history and self.history_index > 0:
            self.history_index -= 1
            self.command_entry.delete(0, tk.END)
            self.command_entry.insert(0, self.history[self.history_index])
        return "break"

    def _history_next(self, _event=None):
        if self.history and self.history_index < len(self.history) - 1:
            self.history_index += 1
            self.command_entry.delete(0, tk.END)
            self.command_entry.insert(0, self.history[self.history_index])
        else:
            self.command_entry.delete(0, tk.END)
        return "break"

    # --------------------------------------------------------------- вывод
    def _poll_output(self):
        """Забирать данные из фонового потока в интерфейс."""
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "state":
                    self._set_state(payload)
                elif kind == "output":
                    self._write_output(payload)
                else:
                    self._write(payload, kind)
        except queue.Empty:
            pass
        self.after(60, self._poll_output)

    def _write_output(self, text):
        """Вывод устройства: с забоем, возвратом каретки и без ANSI-мусора."""
        done, current = ansi.apply_edits(self._line, ansi.clean(text))
        self.output.config(state=tk.NORMAL)
        if self._line:
            self.output.delete("end-1c linestart", "end-1c")
        self.output.insert(tk.END, "".join(done) + current)
        self._line = current
        self._trim()
        self.output.config(state=tk.DISABLED)

    def _write(self, text, tag=None):
        """Сообщение самой программы (подключение, ошибки)."""
        self.output.config(state=tk.NORMAL)
        if self._line:
            self.output.insert(tk.END, "\n")
            self._line = ""
        self.output.insert(tk.END, text, tag or ())
        self._trim()
        self.output.config(state=tk.DISABLED)

    def _trim(self):
        line_count = int(self.output.index("end-1c").split(".")[0])
        if line_count > MAX_LINES:
            self.output.delete("1.0", "%d.0" % (line_count - MAX_LINES))
        self.output.see(tk.END)

    def clear(self):
        self.output.config(state=tk.NORMAL)
        self.output.delete("1.0", tk.END)
        self.output.config(state=tk.DISABLED)
        self._line = ""

    def get_text(self):
        return self.output.get("1.0", tk.END)

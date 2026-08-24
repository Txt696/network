"""
Диалог открытия хранилища: путь + мастер-пароль.

Используется и в NetVault, и в NetMaster — оба приложения работают
с одним и тем же хранилищем.
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from netcore import CryptoError, Vault, VaultError
from netvault import appconfig


class UnlockDialog(tk.Toplevel):
    """Модальное окно: открыть существующее хранилище или создать новое."""

    def __init__(self, parent, initial_path=None, allow_create=True):
        super().__init__(parent)
        self.title("NetVault — открыть хранилище")
        self.resizable(False, False)
        self.result = None
        self._allow_create = allow_create

        path = str(appconfig.resolve_vault_path(initial_path))
        self.path_var = tk.StringVar(value=path)
        self.password_var = tk.StringVar()
        self.show_var = tk.BooleanVar(value=False)

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Хранилище:").grid(row=0, column=0, sticky=tk.W, pady=4)
        path_entry = ttk.Entry(frame, textvariable=self.path_var, width=46)
        path_entry.grid(row=0, column=1, sticky=tk.EW, padx=4)
        ttk.Button(frame, text="Обзор…", command=self._browse).grid(row=0, column=2, padx=2)

        ttk.Label(frame, text="Мастер-пароль:").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.password_entry = ttk.Entry(frame, textvariable=self.password_var, show="•", width=46)
        self.password_entry.grid(row=1, column=1, sticky=tk.EW, padx=4)
        ttk.Checkbutton(frame, text="Показать", variable=self.show_var,
                        command=self._toggle_show).grid(row=1, column=2, padx=2)

        recent = appconfig.load().get("recent", [])
        if recent:
            ttk.Label(frame, text="Недавние:").grid(row=2, column=0, sticky=tk.W, pady=4)
            combo = ttk.Combobox(frame, values=recent, state="readonly", width=44)
            combo.grid(row=2, column=1, sticky=tk.EW, padx=4)
            combo.bind("<<ComboboxSelected>>", lambda e: self.path_var.set(combo.get()))

        self.status = ttk.Label(frame, text="", foreground="#b00020", wraplength=420)
        self.status.grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=3, sticky=tk.E, pady=(10, 0))
        if allow_create:
            ttk.Button(buttons, text="Создать новое…", command=self._create).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Отмена", command=self._cancel).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Открыть", command=self._open).pack(side=tk.LEFT, padx=4)

        frame.columnconfigure(1, weight=1)
        self.bind("<Return>", lambda e: self._open())
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.transient(parent)
        self.password_entry.focus_set()
        self.update_idletasks()
        self.grab_set()

    # ------------------------------------------------------------- действия
    def _toggle_show(self):
        self.password_entry.config(show="" if self.show_var.get() else "•")

    def _browse(self):
        chosen = filedialog.askdirectory(title="Папка хранилища", parent=self)
        if chosen:
            self.path_var.set(chosen)

    def _open(self):
        path = self.path_var.get().strip()
        password = self.password_var.get()
        if not path:
            self.status.config(text="Укажите путь к хранилищу")
            return
        vault = Vault(path)
        if not vault.is_vault:
            self.status.config(text="По этому пути нет хранилища. Нажмите «Создать новое…».")
            return
        if not password:
            self.status.config(text="Введите мастер-пароль")
            return
        try:
            vault.unlock(password)
        except CryptoError as exc:
            self.status.config(text=str(exc))
            self.password_var.set("")
            return
        except (VaultError, OSError, ValueError) as exc:
            self.status.config(text="Не удалось открыть: %s" % exc)
            return
        appconfig.remember_vault(vault.path)
        self.result = vault
        self.destroy()

    def _create(self):
        path = filedialog.askdirectory(
            title="Где создать хранилище (выберите пустую папку)", parent=self)
        if not path:
            return
        if Vault(path).is_vault:
            self.status.config(text="Здесь уже есть хранилище — просто введите пароль")
            self.path_var.set(path)
            return
        password = NewPasswordDialog.ask(
            self, "Новое хранилище",
            "Придумайте мастер-пароль.\nВосстановить его нельзя — запишите в надёжном месте.")
        if not password:
            return
        try:
            vault = Vault.create(path, password)
        except (VaultError, OSError) as exc:
            messagebox.showerror("NetVault", "Не удалось создать хранилище: %s" % exc, parent=self)
            return
        appconfig.remember_vault(vault.path)
        self.result = vault
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    @classmethod
    def ask(cls, parent, initial_path=None, allow_create=True):
        """Показать диалог и вернуть открытое хранилище (или None)."""
        dialog = cls(parent, initial_path, allow_create)
        parent.wait_window(dialog)
        return dialog.result


class NewPasswordDialog(tk.Toplevel):
    """Ввод нового пароля с подтверждением."""

    MIN_LENGTH = 8

    def __init__(self, parent, title, message):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result = None
        self.first = tk.StringVar()
        self.second = tk.StringVar()

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text=message, wraplength=380, justify=tk.LEFT).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))
        ttk.Label(frame, text="Пароль:").grid(row=1, column=0, sticky=tk.W, pady=3)
        entry = ttk.Entry(frame, textvariable=self.first, show="•", width=32)
        entry.grid(row=1, column=1, sticky=tk.EW, padx=4)
        ttk.Label(frame, text="Ещё раз:").grid(row=2, column=0, sticky=tk.W, pady=3)
        ttk.Entry(frame, textvariable=self.second, show="•", width=32).grid(
            row=2, column=1, sticky=tk.EW, padx=4)
        self.status = ttk.Label(frame, text="", foreground="#b00020", wraplength=380)
        self.status.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky=tk.E, pady=(10, 0))
        ttk.Button(buttons, text="Отмена", command=self._cancel).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="OK", command=self._ok).pack(side=tk.LEFT, padx=4)

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.transient(parent)
        entry.focus_set()
        self.update_idletasks()
        self.grab_set()

    def _ok(self):
        if self.first.get() != self.second.get():
            self.status.config(text="Пароли не совпадают")
            return
        if len(self.first.get()) < self.MIN_LENGTH:
            self.status.config(text="Минимум %d символов" % self.MIN_LENGTH)
            return
        self.result = self.first.get()
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    @classmethod
    def ask(cls, parent, title, message):
        dialog = cls(parent, title, message)
        parent.wait_window(dialog)
        return dialog.result


class PasswordPrompt(tk.Toplevel):
    """Однократный запрос мастер-пароля (разблокировка на месте)."""

    def __init__(self, parent, title="Разблокировать", message="Мастер-пароль:"):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result = None
        self.value = tk.StringVar()

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text=message).grid(row=0, column=0, sticky=tk.W)
        entry = ttk.Entry(frame, textvariable=self.value, show="•", width=32)
        entry.grid(row=0, column=1, padx=6)
        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky=tk.E, pady=(10, 0))
        ttk.Button(buttons, text="Отмена", command=self._cancel).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="OK", command=self._ok).pack(side=tk.LEFT, padx=4)

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.transient(parent)
        entry.focus_set()
        self.update_idletasks()
        self.grab_set()

    def _ok(self):
        self.result = self.value.get()
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    @classmethod
    def ask(cls, parent, title="Разблокировать", message="Мастер-пароль:"):
        dialog = cls(parent, title, message)
        parent.wait_window(dialog)
        return dialog.result

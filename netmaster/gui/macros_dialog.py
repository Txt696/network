"""
Редактор сохранённых наборов команд.

Список лежит в хранилище, поэтому один и тот же набор виден и на панели
над терминалом, и в диалоге массовых команд, и на другой машине.
"""

import tkinter as tk
from tkinter import messagebox, ttk

from netmaster.core import macros

VENDORS = ("", "cisco", "huawei", "hp", "juniper", "mikrotik", "arista", "extreme")


class MacrosDialog(tk.Toplevel):
    """Окно правки списка команд."""

    def __init__(self, parent, vault, on_saved=None):
        super().__init__(parent)
        self.title("Наборы команд")
        self.geometry("760x460")
        self.vault = vault
        self.on_saved = on_saved
        self.items = macros.load(vault)
        self.current = None

        self._build_ui()
        self._fill_list()
        self.transient(parent)

    def _build_ui(self):
        panes = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        left = ttk.Frame(panes)
        panes.add(left, weight=1)
        self.listbox = tk.Listbox(left, width=30, exportselection=False)
        self.listbox.pack(fill=tk.BOTH, expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        list_buttons = ttk.Frame(left)
        list_buttons.pack(fill=tk.X, pady=4)
        ttk.Button(list_buttons, text="Добавить", command=self.add).pack(side=tk.LEFT)
        ttk.Button(list_buttons, text="Удалить", command=self.remove).pack(side=tk.LEFT, padx=4)

        right = ttk.Frame(panes)
        panes.add(right, weight=2)

        form = ttk.Frame(right)
        form.pack(fill=tk.X)
        ttk.Label(form, text="Название:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.name_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.name_var, width=36).grid(row=0, column=1, sticky=tk.W)
        ttk.Label(form, text="Вендор:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.vendor = ttk.Combobox(form, values=VENDORS, state="readonly", width=20)
        self.vendor.grid(row=1, column=1, sticky=tk.W)
        ttk.Label(form, text="(пусто — показывать для любого устройства)").grid(
            row=1, column=2, sticky=tk.W, padx=6)

        ttk.Label(right, text="Команды (по одной в строке):").pack(anchor=tk.W, pady=(8, 2))
        self.commands = tk.Text(right, height=12, font=("Consolas", 10))
        self.commands.pack(fill=tk.BOTH, expand=True)

        buttons = ttk.Frame(self, padding=(8, 0, 8, 8))
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Сохранить", command=self.save).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Вернуть встроенные",
                   command=self.reset).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Закрыть", command=self.destroy).pack(side=tk.RIGHT)

    # ------------------------------------------------------------- список
    def _fill_list(self, select=None):
        self.listbox.delete(0, tk.END)
        for item in self.items:
            label = item["name"]
            if item["vendor"]:
                label += "  [%s]" % item["vendor"]
            self.listbox.insert(tk.END, label)
        if self.items:
            index = min(select if select is not None else 0, len(self.items) - 1)
            self.listbox.selection_set(index)
            self._show(index)
        else:
            self.current = None
            self._show_fields({"name": "", "vendor": "", "commands": []})

    def _on_select(self, _event=None):
        selection = self.listbox.curselection()
        if selection:
            self._stash()
            self._show(selection[0])

    def _show(self, index):
        self.current = index
        self._show_fields(self.items[index])

    def _show_fields(self, item):
        self.name_var.set(item["name"])
        self.vendor.set(item["vendor"])
        self.commands.delete("1.0", tk.END)
        self.commands.insert("1.0", "\n".join(item["commands"]))

    def _stash(self):
        """Запомнить правки текущего набора перед переходом к другому."""
        if self.current is None or self.current >= len(self.items):
            return
        self.items[self.current] = {
            "name": self.name_var.get().strip(),
            "vendor": self.vendor.get().strip().lower(),
            "commands": [line.strip() for line in
                         self.commands.get("1.0", tk.END).splitlines() if line.strip()],
        }

    # ------------------------------------------------------------ действия
    def add(self):
        self._stash()
        self.items.append({"name": "Новый набор", "vendor": "", "commands": ["show version"]})
        self._fill_list(select=len(self.items) - 1)

    def remove(self):
        if self.current is None or self.current >= len(self.items):
            return
        del self.items[self.current]
        self.current = None
        self._fill_list()

    def reset(self):
        if not messagebox.askyesno("NetMaster", "Заменить список встроенным набором?",
                                   parent=self):
            return
        self.items = macros.load()
        self.current = None
        self._fill_list()

    def save(self):
        self._stash()
        saved = macros.save(self.vault, self.items)
        dropped = len(self.items) - len(saved)
        self.items = saved
        self._fill_list(select=self.current)
        if self.on_saved:
            self.on_saved(saved)
        message = "Сохранено наборов: %d" % len(saved)
        if dropped:
            message += "\nПропущено пустых: %d" % dropped
        messagebox.showinfo("NetMaster", message, parent=self)

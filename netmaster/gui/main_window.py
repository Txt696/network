"""
Главное окно NetMaster.

Устройства берутся из хранилища NetVault: слева дерево и карточка
с данными, справа вкладки SSH-сессий. Пароли подставляются из
зашифрованного хранилища — вводить их вручную не нужно.
"""

import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from netcore.secretstore import LockedError
from netmaster.core import network_tools
from netmaster.core.inventory import Inventory
from netmaster.gui.bulk_dialog import BulkDialog
from netmaster.gui.terminal_widget import TerminalWidget
from netmaster.gui.tools_dialog import ToolsDialog
from netvault.gui.unlock_dialog import PasswordPrompt, UnlockDialog

APP_TITLE = "NetMaster"

CONFIG_COMMANDS = {
    "cisco": "show running-config",
    "arista": "show running-config",
    "huawei": "display current-configuration",
    "hp": "display current-configuration",
    "juniper": "show configuration | display set",
    "mikrotik": "/export",
}


class MainWindow:
    def __init__(self, root, vault=None, autoconnect_device=None):
        self.root = root
        self.inventory = Inventory(vault) if vault else None
        self.devices = []
        self.sessions = {}
        self.search_var = tk.StringVar()

        self._setup_style()
        self._build_menu()
        self._build_layout()
        self._build_statusbar()

        self.search_var.trace_add("write", lambda *_: self.refresh_tree())
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<F5>", lambda e: self.refresh_tree())
        self.root.bind("<Control-f>", lambda e: self.search_entry.focus_set())

        if self.inventory:
            self.refresh_tree()
            if autoconnect_device:
                self.connect_device(autoconnect_device)
        self._update_status()

    # ------------------------------------------------------------- интерфейс
    def _setup_style(self):
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Connected.TLabel", foreground="#1b7f3b", font=("Arial", 9, "bold"))
        style.configure("Disconnected.TLabel", foreground="#b00020", font=("Arial", 9))

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        vault_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Хранилище", menu=vault_menu)
        vault_menu.add_command(label="Открыть…", command=self.open_vault)
        vault_menu.add_command(label="Разблокировать", command=self.ensure_unlocked)
        vault_menu.add_command(label="Заблокировать", command=self.lock_vault)
        vault_menu.add_command(label="Обновить список (F5)", command=self.refresh_tree)
        vault_menu.add_separator()
        vault_menu.add_command(label="Открыть NetVault (редактор данных)",
                               command=self.launch_netvault)
        vault_menu.add_command(label="Выход", command=self.on_close)

        session_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Сессия", menu=session_menu)
        session_menu.add_command(label="Подключиться к выбранному", command=self.connect_selected)
        session_menu.add_command(label="Закрыть вкладку", command=self.close_current_tab)

        bulk_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Массовые операции", menu=bulk_menu)
        bulk_menu.add_command(label="Выполнить команды…", command=self.bulk_commands)
        bulk_menu.add_command(label="Собрать конфигурации", command=self.collect_configs)
        bulk_menu.add_command(label="Ping выбранных", command=self.ping_selected)
        bulk_menu.add_command(label="Проверить доступность SSH", command=self.check_ssh_ports)

        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Инструменты", menu=tools_menu)
        tools_menu.add_command(label="Сетевые утилиты…", command=self.open_tools)

        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Справка", menu=help_menu)
        help_menu.add_command(label="О программе", command=self.show_about)

    def _build_layout(self):
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        search_frame = ttk.Frame(left)
        search_frame.pack(fill=tk.X)
        ttk.Label(search_frame, text="Поиск:").pack(side=tk.LEFT)
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Button(search_frame, text="×", width=3,
                   command=lambda: self.search_var.set("")).pack(side=tk.LEFT)

        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        self.tree = ttk.Treeview(tree_frame, columns=("ip",), show="tree headings",
                                 selectmode="extended")
        self.tree.heading("#0", text="Устройство")
        self.tree.heading("ip", text="IP")
        self.tree.column("#0", width=210)
        self.tree.column("ip", width=120, stretch=False)
        scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.LEFT, fill=tk.Y)
        self.tree.bind("<Double-1>", lambda e: self.connect_selected())
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        buttons = ttk.Frame(left)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Подключиться", command=self.connect_selected).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Команды…", command=self.bulk_commands).pack(side=tk.LEFT, padx=4)

        info = ttk.LabelFrame(left, text="Данные из хранилища", padding=6)
        info.pack(fill=tk.X, pady=6)
        self.info_text = tk.Text(info, height=11, wrap=tk.WORD, font=("Consolas", 9))
        self.info_text.pack(fill=tk.BOTH, expand=True)
        self.info_text.config(state=tk.DISABLED)

        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        welcome = ttk.Frame(self.notebook)
        self.notebook.add(welcome, text="Начало")
        ttk.Label(
            welcome,
            text="NetMaster — управление оборудованием по данным из NetVault.\n\n"
                 "Выберите устройство слева и нажмите «Подключиться»:\n"
                 "логин и пароль подставятся из хранилища.\n\n"
                 "Несколько устройств (Ctrl/Shift) → «Команды…» для массовых операций.",
            justify=tk.LEFT, font=("Arial", 11)).pack(expand=True, padx=20)

    def _build_statusbar(self):
        bar = ttk.Frame(self.root)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_label = ttk.Label(bar, text="", anchor=tk.W)
        self.status_label.pack(side=tk.LEFT, padx=6)
        self.vault_label = ttk.Label(bar, text="", anchor=tk.E)
        self.vault_label.pack(side=tk.RIGHT, padx=6)

    # -------------------------------------------------------------- хранилище
    def open_vault(self):
        current = str(self.inventory.vault.path) if self.inventory else None
        vault = UnlockDialog.ask(self.root, current)
        if vault:
            self.inventory = Inventory(vault)
            self.refresh_tree()
            self._update_status()

    def ensure_unlocked(self):
        if not self.inventory:
            self.open_vault()
            return bool(self.inventory) and not self.inventory.is_locked
        if not self.inventory.is_locked:
            return True
        password = PasswordPrompt.ask(self.root, APP_TITLE, "Мастер-пароль хранилища:")
        if password is None:
            return False
        try:
            self.inventory.unlock(password)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, "Не удалось открыть хранилище: %s" % exc,
                                 parent=self.root)
            return False
        self._update_status()
        return True

    def lock_vault(self):
        if self.inventory:
            self.inventory.vault.lock()
        self._update_status()

    def launch_netvault(self):
        import subprocess
        import sys
        root_dir = Path(__file__).resolve().parents[2]
        command = [sys.executable, str(root_dir / "netvault" / "main.py")]
        if self.inventory:
            command += ["--vault", str(self.inventory.vault.path)]
        try:
            subprocess.Popen(command, cwd=str(root_dir))
        except OSError as exc:
            messagebox.showerror(APP_TITLE, "Не удалось запустить NetVault: %s" % exc,
                                 parent=self.root)

    # ------------------------------------------------------------------ дерево
    def refresh_tree(self):
        if not self.inventory:
            return
        self.tree.delete(*self.tree.get_children())
        self.devices = self.inventory.devices(self.search_var.get())
        groups = {}
        for device in self.devices:
            groups.setdefault(device.site or "Без площадки", []).append(device)
        for site in sorted(groups):
            node = self.tree.insert("", tk.END, iid="group::" + site,
                                    text="%s (%d)" % (site, len(groups[site])), open=True)
            for device in groups[site]:
                self.tree.insert(node, tk.END, iid=device.id, text=device.name,
                                 values=(device.target or "—",))
        self._update_status()

    def selected_ids(self):
        return [iid for iid in self.tree.selection() if not iid.startswith("group::")]

    def selected_devices(self):
        ids = set(self.selected_ids())
        return [d for d in self.devices if d.id in ids]

    def on_select(self, _event=None):
        devices = self.selected_devices()
        self.info_text.config(state=tk.NORMAL)
        self.info_text.delete("1.0", tk.END)
        if len(devices) == 1:
            device = devices[0]
            lines = [
                "%s" % device.name,
                "адрес:    %s:%s (%s)" % (device.target or "—", device.port, device.protocol),
                "тип:      %s" % device.kind,
                "вендор:   %s %s" % (device.vendor or "—", device.model or ""),
                "версия:   %s" % (device.os_version or "—"),
                "площадка: %s %s" % (device.site or "—", ("стойка " + device.rack) if device.rack else ""),
                "роль:     %s" % (device.role or "—"),
                "теги:     %s" % (", ".join(device.tags) or "—"),
                "аплинки:  %s" % (", ".join(device.uplinks) or "—"),
                "серийник: %s" % (device.serial or "—"),
            ]
            if device.body.strip():
                lines += ["", "заметка:", device.body.strip()[:600]]
            self.info_text.insert("1.0", "\n".join(lines))
        elif devices:
            self.info_text.insert("1.0", "Выбрано устройств: %d\n\n%s" % (
                len(devices), "\n".join("%s — %s" % (d.name, d.target or "—") for d in devices)))
        self.info_text.config(state=tk.DISABLED)

    # ------------------------------------------------------------------ сессии
    def connect_selected(self):
        devices = self.selected_devices()
        if not devices:
            return
        if len(devices) > 3 and not messagebox.askyesno(
                APP_TITLE, "Открыть %d сессий?" % len(devices), parent=self.root):
            return
        for device in devices:
            self.connect_device(device.id)

    def connect_device(self, device_id):
        if not self.ensure_unlocked():
            return
        try:
            target = self.inventory.target(device_id)
        except (KeyError, LockedError) as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self.root)
            return
        if not target.host:
            messagebox.showwarning(APP_TITLE, "У устройства нет адреса — заполните его в NetVault",
                                   parent=self.root)
            return
        if not target.has_credentials:
            messagebox.showwarning(
                APP_TITLE,
                "Для %s в хранилище нет логина/пароля.\nДобавьте их в NetVault (вкладка «Доступы»)."
                % target.name, parent=self.root)
            return
        terminal = TerminalWidget(self.notebook, target=target,
                                  on_state_change=self._on_session_state)
        self.notebook.add(terminal, text=target.name)
        self.notebook.select(terminal)
        self.sessions[str(terminal)] = terminal
        terminal.connect()
        self._update_status()

    def _on_session_state(self, _terminal, _connected):
        self._update_status()

    def close_current_tab(self):
        current = self.notebook.select()
        if not current:
            return
        widget = self.notebook.nametowidget(current)
        if isinstance(widget, TerminalWidget):
            widget.disconnect()
            self.sessions.pop(str(widget), None)
        self.notebook.forget(current)
        self._update_status()

    # -------------------------------------------------------- массовые операции
    def _targets_for_selection(self, require=True):
        if not self.ensure_unlocked():
            return []
        devices = self.selected_devices() or (self.devices if not require else [])
        if not devices:
            messagebox.showinfo(APP_TITLE, "Выберите устройства в списке", parent=self.root)
            return []
        return self.inventory.targets(devices)

    def bulk_commands(self):
        targets = self._targets_for_selection()
        if targets:
            BulkDialog(self.root, self.inventory, targets)

    def collect_configs(self):
        targets = self._targets_for_selection()
        if not targets:
            return
        dialog = BulkDialog(self.root, self.inventory, targets,
                            title="Сбор конфигураций в хранилище")
        vendors = {t.vendor.split()[0] if t.vendor else "" for t in targets}
        command = None
        for vendor in vendors:
            for key, value in CONFIG_COMMANDS.items():
                if vendor and key in vendor:
                    command = value
        dialog.commands.insert("1.0", command or "show running-config")
        dialog.save_var.set(True)
        dialog.enable_var.set(True)

    def ping_selected(self):
        targets = self._targets_for_selection()
        if not targets:
            return
        window = tk.Toplevel(self.root)
        window.title("Ping выбранных устройств")
        window.geometry("560x400")
        output = tk.Text(window, font=("Consolas", 10))
        output.pack(fill=tk.BOTH, expand=True)

        def work():
            for target in targets:
                if not target.host:
                    line = "%-24s нет адреса\n" % target.name
                else:
                    result = network_tools.PingTool().ping(target.host, count=2)
                    stats = result.get("stats", {})
                    line = "%-24s %-15s %s  loss=%s%% avg=%s\n" % (
                        target.name[:24], target.host,
                        "доступен" if result.get("success") else "НЕДОСТУПЕН",
                        stats.get("loss", "?"), stats.get("avg", "?"))
                window.after(0, lambda t=line: (output.insert(tk.END, t), output.see(tk.END)))
        threading.Thread(target=work, daemon=True).start()

    def check_ssh_ports(self):
        targets = self._targets_for_selection()
        if not targets:
            return
        window = tk.Toplevel(self.root)
        window.title("Доступность портов управления")
        window.geometry("560x400")
        output = tk.Text(window, font=("Consolas", 10))
        output.pack(fill=tk.BOTH, expand=True)

        def work():
            scanner = network_tools.PortScanner()
            for target in targets:
                if not target.host:
                    continue
                result = scanner.scan(target.host, [target.port])
                open_ports = result.get("results", {}).get("open", [])
                line = "%-24s %-15s порт %s: %s\n" % (
                    target.name[:24], target.host, target.port,
                    "открыт" if open_ports else "закрыт")
                window.after(0, lambda t=line: (output.insert(tk.END, t), output.see(tk.END)))
        threading.Thread(target=work, daemon=True).start()

    def open_tools(self):
        devices = self.selected_devices()
        ToolsDialog(self.root, devices[0].target if devices else "")

    # ---------------------------------------------------------------- сервис
    def _update_status(self):
        active = sum(1 for t in self.sessions.values() if t.connected)
        self.status_label.config(text="Устройств: %d | Сессий: %d (активных: %d)" % (
            len(self.devices), len(self.sessions), active))
        if not self.inventory:
            self.vault_label.config(text="Хранилище не открыто", style="Disconnected.TLabel")
        elif self.inventory.is_locked:
            self.vault_label.config(text="🔒 %s" % self.inventory.vault.path,
                                    style="Disconnected.TLabel")
        else:
            self.vault_label.config(text="🔓 %s" % self.inventory.vault.path,
                                    style="Connected.TLabel")

    def show_about(self):
        messagebox.showinfo(
            APP_TITLE,
            "NetMaster — управление серверами, свитчами и роутерами.\n\n"
            "Данные и пароли берутся из хранилища NetVault:\n"
            "подключение в один клик, массовые команды,\n"
            "сбор конфигураций обратно в хранилище.",
            parent=self.root)

    def on_close(self):
        for terminal in list(self.sessions.values()):
            terminal.disconnect()
        if self.inventory:
            self.inventory.vault.lock()
        self.root.destroy()

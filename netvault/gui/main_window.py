"""
Главное окно NetVault: слева дерево устройств, справа карточка
(данные, заметка в Markdown, доступы, собранные с устройства файлы).
"""

import secrets as pysecrets
import string
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from netcore import Device, KINDS, PROTOCOLS, STATUSES, Vault, VaultError
from netcore import launcher, links, ports
from netcore.models import DEFAULT_PORTS, slugify
from netcore.secretstore import FIELDS as SECRET_FIELDS, LockedError
from netvault import APP_NAME, __version__, appconfig
from netvault.gui.unlock_dialog import NewPasswordDialog, PasswordPrompt, UnlockDialog

# Поля карточки: (ключ, подпись, тип виджета, варианты)
FORM_FIELDS = (
    ("name", "Название", "entry", None),
    ("kind", "Тип", "combo", KINDS),
    ("mgmt_ip", "IP управления", "entry", None),
    ("hostname", "Hostname", "entry", None),
    ("vendor", "Вендор", "entry", None),
    ("model", "Модель", "entry", None),
    ("os_version", "Версия ПО", "entry", None),
    ("serial", "Серийный номер", "entry", None),
    ("site", "Площадка", "entry", None),
    ("room", "Помещение", "entry", None),
    ("rack", "Стойка", "entry", None),
    ("unit", "Юнит", "entry", None),
    ("role", "Роль", "entry", None),
    ("protocol", "Протокол", "combo", PROTOCOLS),
    ("port", "Порт", "entry", None),
    ("status", "Статус", "combo", STATUSES),
    ("owner", "Ответственный", "entry", None),
    ("secret", "Ссылка на доступы", "entry", None),
    ("tags", "Теги (через запятую)", "entry", None),
)
LIST_FIELDS = ("tags",)

# Варианты для списка типов портов: "Gi — GigabitEthernet, 1 Гбит".
PORT_TYPE_CHOICES = ["%s — %s" % (code, text) for code, text in ports.PORT_TYPES]
PORT_TYPE_CODES = {choice: code for choice, (code, _) in
                   zip(PORT_TYPE_CHOICES, ports.PORT_TYPES)}

KIND_LABELS = {
    "server": "Серверы", "switch": "Свитчи", "router": "Роутеры",
    "firewall": "Файрволы", "ap": "Точки доступа", "storage": "СХД",
    "pdu": "PDU", "other": "Прочее",
}


class MainWindow:
    def __init__(self, root, vault=None):
        self.root = root
        self.vault = vault
        self.current_id = None
        self.devices = []
        self._suppress_select = False
        self._clipboard_token = 0

        self.search_var = tk.StringVar()
        self.group_var = tk.StringVar(value="site")
        self.field_vars = {key: tk.StringVar() for key, _, _, _ in FORM_FIELDS}
        self.secret_vars = {key: tk.StringVar() for key in SECRET_FIELDS}
        self.show_secrets = tk.BooleanVar(value=False)

        # Порты: группы («Gi1/0/1-48») и настройка каждого порта отдельно.
        self.port_groups = []
        self.port_config = {}
        self.port_type_var = tk.StringVar(value=PORT_TYPE_CHOICES[1])
        self.port_path_var = tk.StringVar(value="1/0")
        self.port_count_var = tk.StringVar(value="48")
        self.port_first_var = tk.StringVar(value="1")
        self.port_mode_var = tk.StringVar()
        self.port_vlans_var = tk.StringVar()
        self.port_peer_var = tk.StringVar()
        self.port_peer_port_var = tk.StringVar()
        self.device_vlans_var = tk.StringVar()
        self.plain_uplinks_var = tk.StringVar()
        self.only_configured = tk.BooleanVar(value=False)

        self._setup_style()
        self._build_menu()
        self._build_layout()
        self._build_statusbar()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Control-s>", lambda e: self.save_current())
        self.root.bind("<Control-n>", lambda e: self.new_device())
        self.root.bind("<Control-f>", lambda e: self.search_entry.focus_set())
        self.root.bind("<Control-l>", lambda e: self.lock_vault())

        self.search_var.trace_add("write", lambda *_: self.refresh_tree(keep_selection=True))
        self._tick()
        if self.vault:
            self.refresh_tree()
        self._update_status()

    # ------------------------------------------------------------- интерфейс
    def _setup_style(self):
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Locked.TLabel", foreground="#b00020")
        style.configure("Unlocked.TLabel", foreground="#1b7f3b")
        style.configure("Group.Treeview", rowheight=22)

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        vault_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Хранилище", menu=vault_menu)
        vault_menu.add_command(label="Открыть…", command=self.open_vault)
        vault_menu.add_command(label="Заблокировать", command=self.lock_vault, accelerator="Ctrl+L")
        vault_menu.add_command(label="Сменить мастер-пароль…", command=self.change_master_password)
        vault_menu.add_separator()
        vault_menu.add_command(label="Экспорт CSV…", command=lambda: self.export("csv"))
        vault_menu.add_command(label="Экспорт JSON…", command=lambda: self.export("json"))
        vault_menu.add_command(label="Открыть папку хранилища", command=self.open_folder)
        vault_menu.add_separator()
        vault_menu.add_command(label="Выход", command=self.on_close)

        device_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Устройство", menu=device_menu)
        device_menu.add_command(label="Новое", command=self.new_device, accelerator="Ctrl+N")
        device_menu.add_command(label="Сохранить", command=self.save_current, accelerator="Ctrl+S")
        device_menu.add_command(label="Дублировать", command=self.duplicate_device)
        device_menu.add_command(label="Удалить", command=self.delete_device)
        device_menu.add_separator()
        device_menu.add_command(label="Открыть заметку в системе", command=self.open_note_externally)

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Вид", menu=view_menu)
        view_menu.add_radiobutton(label="Группировать по площадке", value="site",
                                  variable=self.group_var, command=self.refresh_tree)
        view_menu.add_radiobutton(label="Группировать по типу", value="kind",
                                  variable=self.group_var, command=self.refresh_tree)
        view_menu.add_radiobutton(label="Без группировки", value="flat",
                                  variable=self.group_var, command=self.refresh_tree)
        view_menu.add_separator()
        view_menu.add_command(label="Обновить", command=self.refresh_tree, accelerator="F5")
        view_menu.add_command(label="Проверить хранилище", command=self.show_doctor)
        view_menu.add_command(label="Топология (аплинки)", command=self.show_topology)
        self.root.bind("<F5>", lambda e: self.refresh_tree())

        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Управление", menu=tools_menu)
        tools_menu.add_command(label="Карта сети в браузере", command=self.launch_netmap)
        tools_menu.add_command(label="Открыть NetMaster с этим хранилищем",
                               command=self.launch_netmaster)

        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Справка", menu=help_menu)
        help_menu.add_command(label="О программе", command=self.show_about)

    def _build_layout(self):
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        search_frame = ttk.Frame(left)
        search_frame.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(search_frame, text="Поиск:").pack(side=tk.LEFT)
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Button(search_frame, text="×", width=3,
                   command=lambda: self.search_var.set("")).pack(side=tk.LEFT)

        self.tree = ttk.Treeview(left, columns=("ip", "kind"), show="tree headings", height=25)
        self.tree.heading("#0", text="Устройство")
        self.tree.heading("ip", text="IP")
        self.tree.heading("kind", text="Тип")
        self.tree.column("#0", width=210, stretch=True)
        self.tree.column("ip", width=115, stretch=False)
        self.tree.column("kind", width=80, stretch=False)
        scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.LEFT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        buttons = ttk.Frame(left)
        buttons.pack(fill=tk.X, pady=4)
        ttk.Button(buttons, text="+ Устройство", command=self.new_device).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Удалить", command=self.delete_device).pack(side=tk.LEFT, padx=4)

        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        self.title_label = ttk.Label(right, text="Выберите устройство", font=("Arial", 13, "bold"))
        self.title_label.pack(anchor=tk.W, pady=(0, 6))

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.notebook.add(self._build_form_tab(), text="Данные")
        self.notebook.add(self._build_ports_tab(), text="Порты (аплинки и VLAN)")
        self.notebook.add(self._build_note_tab(), text="Заметка")
        self.notebook.add(self._build_secret_tab(), text="Доступы")
        self.notebook.add(self._build_collected_tab(), text="Собранное")

        actions = ttk.Frame(right)
        actions.pack(fill=tk.X, pady=6)
        ttk.Button(actions, text="Сохранить (Ctrl+S)", command=self.save_current).pack(side=tk.LEFT)
        ttk.Button(actions, text="Отменить правки", command=self.reload_current).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Подключиться в NetMaster",
                   command=self.launch_netmaster_for_device).pack(side=tk.RIGHT)
        self._refresh_port_groups()

    def _build_form_tab(self):
        frame = ttk.Frame(self.notebook, padding=8)
        columns = 2
        per_column = (len(FORM_FIELDS) + columns - 1) // columns
        for index, (key, label, kind, options) in enumerate(FORM_FIELDS):
            col = index // per_column
            row = index % per_column
            ttk.Label(frame, text=label + ":").grid(
                row=row, column=col * 2, sticky=tk.W, padx=(4, 6), pady=3)
            if kind == "combo":
                widget = ttk.Combobox(frame, textvariable=self.field_vars[key],
                                      values=list(options), state="readonly", width=26)
                if key == "protocol":
                    widget.bind("<<ComboboxSelected>>", self._on_protocol_change)
            else:
                widget = ttk.Entry(frame, textvariable=self.field_vars[key], width=28)
            widget.grid(row=row, column=col * 2 + 1, sticky=tk.EW, padx=(0, 12), pady=3)
        for col in range(columns):
            frame.columnconfigure(col * 2 + 1, weight=1)
        self._build_port_groups(frame).grid(
            row=per_column, column=0, columnspan=4, sticky=tk.EW, pady=(12, 0))
        return frame

    def _build_port_groups(self, parent):
        """Сколько на устройстве портов и как они называются (Gi1/0/1-48)."""
        box = ttk.LabelFrame(parent, text="Порты устройства", padding=6)

        line = ttk.Frame(box)
        line.pack(fill=tk.X)
        ttk.Label(line, text="Тип:").grid(row=0, column=0, sticky=tk.W, padx=(0, 4))
        ttk.Combobox(line, textvariable=self.port_type_var, values=PORT_TYPE_CHOICES,
                     state="readonly", width=24).grid(row=0, column=1, sticky=tk.W)
        ttk.Label(line, text="Количество:").grid(row=0, column=2, sticky=tk.W, padx=(16, 4))
        ttk.Spinbox(line, textvariable=self.port_count_var, from_=1, to=ports.MAX_PORTS,
                    width=5).grid(row=0, column=3, sticky=tk.W)
        ttk.Button(line, text="Добавить группу", command=self.add_port_group).grid(
            row=0, column=4, rowspan=2, padx=(16, 0))
        ttk.Label(line, text="Шасси/модуль:").grid(row=1, column=0, sticky=tk.W, padx=(0, 4),
                                                   pady=(4, 0))
        ttk.Entry(line, textvariable=self.port_path_var, width=8).grid(
            row=1, column=1, sticky=tk.W, pady=(4, 0))
        ttk.Label(line, text="С номера:").grid(row=1, column=2, sticky=tk.W, padx=(16, 4),
                                               pady=(4, 0))
        ttk.Spinbox(line, textvariable=self.port_first_var, from_=0, to=ports.MAX_PORTS,
                    width=5).grid(row=1, column=3, sticky=tk.W, pady=(4, 0))

        body = ttk.Frame(box)
        body.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.port_group_list = tk.Listbox(body, height=4, exportselection=False)
        self.port_group_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        side = ttk.Frame(body)
        side.pack(side=tk.LEFT, padx=6)
        ttk.Button(side, text="Удалить", width=12, command=self.remove_port_group).pack()
        ttk.Button(side, text="Очистить", width=12, command=self.clear_port_groups).pack(pady=4)

        self.port_total_label = ttk.Label(box, text="", foreground="#666")
        self.port_total_label.pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(box, text="Свитч на 48 гигабитов и 4 десятки — это две группы: "
                            "Gi 1/0 × 48 и Te 1/0 × 4 с номера 49. Настройка каждого порта "
                            "отдельно — на вкладке «Порты (аплинки и VLAN)».",
                  foreground="#666", wraplength=560, justify=tk.LEFT).pack(anchor=tk.W)
        return box

    def _build_ports_tab(self):
        """Таблица портов: у каждого свой аплинк и свои VLAN."""
        frame = ttk.Frame(self.notebook, padding=6)

        top = ttk.Frame(frame)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Выберите порт (или несколько) и настройте его ниже.",
                  foreground="#666").pack(side=tk.LEFT)
        ttk.Checkbutton(top, text="Только настроенные", variable=self.only_configured,
                        command=self.refresh_ports_table).pack(side=tk.RIGHT)

        table = ttk.Frame(frame)
        table.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.ports_tree = ttk.Treeview(table, columns=("mode", "vlans", "peer", "peer_port"),
                                       show="tree headings", height=12, selectmode="extended")
        self.ports_tree.heading("#0", text="Порт")
        self.ports_tree.heading("mode", text="Режим")
        self.ports_tree.heading("vlans", text="VLAN")
        self.ports_tree.heading("peer", text="Аплинк — сосед")
        self.ports_tree.heading("peer_port", text="Порт соседа")
        self.ports_tree.column("#0", width=120, stretch=False)
        self.ports_tree.column("mode", width=70, stretch=False)
        self.ports_tree.column("vlans", width=130, stretch=False)
        self.ports_tree.column("peer", width=180, stretch=True)
        self.ports_tree.column("peer_port", width=110, stretch=False)
        self.ports_tree.tag_configure("set", background="#eef7ee")
        self.ports_tree.tag_configure("extra", background="#fff4e2")
        ports_scroll = ttk.Scrollbar(table, orient=tk.VERTICAL, command=self.ports_tree.yview)
        self.ports_tree.configure(yscrollcommand=ports_scroll.set)
        self.ports_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ports_scroll.pack(side=tk.LEFT, fill=tk.Y)
        self.ports_tree.bind("<<TreeviewSelect>>", self.on_port_select)

        editor = ttk.LabelFrame(frame, text="Настройка порта", padding=6)
        editor.pack(fill=tk.X, pady=(8, 0))
        self.port_selection_label = ttk.Label(editor, text="Порт не выбран", foreground="#666")
        self.port_selection_label.grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 6))

        ttk.Label(editor, text="Режим:").grid(row=1, column=0, sticky=tk.W, padx=(0, 6))
        ttk.Combobox(editor, textvariable=self.port_mode_var, state="readonly", width=12,
                     values=("",) + ports.VLAN_MODES).grid(row=1, column=1, sticky=tk.W)
        ttk.Label(editor, text="VLAN (через пробел):").grid(row=1, column=2, sticky=tk.W, padx=(16, 6))
        ttk.Entry(editor, textvariable=self.port_vlans_var, width=28).grid(
            row=1, column=3, sticky=tk.EW)

        ttk.Label(editor, text="Сосед:").grid(row=2, column=0, sticky=tk.W, padx=(0, 6), pady=(6, 0))
        self.peer_combo = ttk.Combobox(editor, textvariable=self.port_peer_var, width=12)
        self.peer_combo.grid(row=2, column=1, sticky=tk.EW, pady=(6, 0))
        ttk.Label(editor, text="Порт соседа:").grid(row=2, column=2, sticky=tk.W, padx=(16, 6),
                                                    pady=(6, 0))
        self.peer_port_entry = ttk.Entry(editor, textvariable=self.port_peer_port_var, width=28)
        self.peer_port_entry.grid(row=2, column=3, sticky=tk.EW, pady=(6, 0))

        buttons = ttk.Frame(editor)
        buttons.grid(row=3, column=0, columnspan=4, sticky=tk.W, pady=(8, 0))
        ttk.Button(buttons, text="Применить к выбранным",
                   command=self.apply_port_settings).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Очистить выбранные",
                   command=self.clear_port_settings).pack(side=tk.LEFT, padx=6)
        ttk.Label(editor, text="Режим и VLAN применяются сразу ко всем выбранным портам. "
                               "Аплинк у каждого порта свой, поэтому сосед и его порт "
                               "меняются, только когда выбран ровно один порт.",
                  foreground="#666", wraplength=560, justify=tk.LEFT).grid(
            row=4, column=0, columnspan=4, sticky=tk.W, pady=(6, 0))
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=1)

        rest = ttk.Frame(frame)
        rest.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(rest, text="VLAN всего устройства (через запятую):").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 6))
        ttk.Entry(rest, textvariable=self.device_vlans_var).grid(row=0, column=1, sticky=tk.EW)
        ttk.Label(rest, text="Аплинки без указания порта (через запятую):").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 6), pady=(4, 0))
        ttk.Entry(rest, textvariable=self.plain_uplinks_var).grid(row=1, column=1, sticky=tk.EW,
                                                                  pady=(4, 0))
        rest.columnconfigure(1, weight=1)
        return frame

    def _build_note_tab(self):
        frame = ttk.Frame(self.notebook, padding=6)
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text="Свободные заметки (Markdown, открывается в Obsidian)",
                  foreground="#666").pack(side=tk.LEFT)
        for title, template in (
            ("Что настроено", "\n## Что настроено\n\n"),
            ("Изменение", "\n## %s — изменение\n\n"),
        ):
            ttk.Button(toolbar, text="+ " + title, width=16,
                       command=lambda t=template: self._insert_template(t)).pack(side=tk.RIGHT, padx=2)

        self.note_text = tk.Text(frame, wrap=tk.WORD, undo=True, font=("Consolas", 10),
                                 background="#fbfbfb")
        note_scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.note_text.yview)
        self.note_text.configure(yscrollcommand=note_scroll.set)
        self.note_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(6, 0))
        note_scroll.pack(side=tk.LEFT, fill=tk.Y, pady=(6, 0))
        return frame

    def _build_secret_tab(self):
        frame = ttk.Frame(self.notebook, padding=8)
        labels = {
            "username": "Логин", "password": "Пароль", "enable_password": "Enable / root",
            "snmp_community": "SNMP community", "private_key_path": "Путь к ключу",
            "key_passphrase": "Пароль ключа", "api_token": "API-токен", "notes": "Примечание",
        }
        self.secret_entries = {}
        for row, key in enumerate(SECRET_FIELDS):
            ttk.Label(frame, text=labels.get(key, key) + ":").grid(
                row=row, column=0, sticky=tk.W, pady=4, padx=(4, 6))
            entry = ttk.Entry(frame, textvariable=self.secret_vars[key], width=40, show="•")
            entry.grid(row=row, column=1, sticky=tk.EW, pady=4)
            self.secret_entries[key] = entry
            ttk.Button(frame, text="Копировать", width=12,
                       command=lambda k=key: self.copy_secret(k)).grid(row=row, column=2, padx=4)
        for key in ("private_key_path", "notes", "username"):
            self.secret_entries[key].config(show="")

        row = len(SECRET_FIELDS)
        ttk.Checkbutton(frame, text="Показать пароли", variable=self.show_secrets,
                        command=self._toggle_secret_visibility).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=(10, 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=row + 1, column=0, columnspan=3, sticky=tk.W, pady=8)
        ttk.Button(buttons, text="Сохранить доступы", command=self.save_secret).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Сгенерировать пароль",
                   command=self.generate_password).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Удалить доступы", command=self.delete_secret).pack(side=tk.LEFT)
        self.secret_hint = ttk.Label(frame, text="", foreground="#666", wraplength=520)
        self.secret_hint.grid(row=row + 2, column=0, columnspan=3, sticky=tk.W)
        frame.columnconfigure(1, weight=1)
        return frame

    def _build_collected_tab(self):
        frame = ttk.Frame(self.notebook, padding=6)
        top = ttk.Frame(frame)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Файлы, собранные NetMaster (конфиги, вывод команд)",
                  foreground="#666").pack(side=tk.LEFT)
        ttk.Button(top, text="Обновить", command=self.refresh_collected).pack(side=tk.RIGHT)

        self.collected_list = tk.Listbox(frame, height=6)
        self.collected_list.pack(fill=tk.X, pady=6)
        self.collected_list.bind("<<ListboxSelect>>", self.show_collected)

        self.collected_text = tk.Text(frame, wrap=tk.NONE, font=("Consolas", 9),
                                      background="#1e1e1e", foreground="#dcdcdc")
        self.collected_text.pack(fill=tk.BOTH, expand=True)
        self.collected_text.config(state=tk.DISABLED)
        self._collected_paths = []
        return frame

    def _build_statusbar(self):
        bar = ttk.Frame(self.root)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_label = ttk.Label(bar, text="", anchor=tk.W)
        self.status_label.pack(side=tk.LEFT, padx=6)
        self.lock_label = ttk.Label(bar, text="", style="Locked.TLabel")
        self.lock_label.pack(side=tk.RIGHT, padx=6)

    # -------------------------------------------------------------- хранилище
    def open_vault(self):
        if not self._confirm_discard():
            return
        vault = UnlockDialog.ask(self.root, str(self.vault.path) if self.vault else None)
        if vault:
            self.vault = vault
            self.current_id = None
            self._clear_form()
            self.refresh_tree()
            self._update_status()

    def lock_vault(self):
        if self.vault and not self.vault.is_locked:
            self.vault.lock()
        for var in self.secret_vars.values():
            var.set("")
        self.show_secrets.set(False)
        self._toggle_secret_visibility()
        self._update_status()

    def ensure_unlocked(self):
        """Разблокировать хранилище, спросив пароль. True — открыто."""
        if not self.vault:
            self.open_vault()
            return bool(self.vault) and not self.vault.is_locked
        if not self.vault.is_locked:
            return True
        password = PasswordPrompt.ask(self.root, "NetVault", "Мастер-пароль:")
        if password is None:
            return False
        try:
            self.vault.unlock(password)
        except Exception as exc:
            messagebox.showerror(APP_NAME, "Не удалось открыть: %s" % exc, parent=self.root)
            return False
        self._update_status()
        return True

    def change_master_password(self):
        if not self.vault:
            return
        old = PasswordPrompt.ask(self.root, "Смена пароля", "Текущий мастер-пароль:")
        if old is None:
            return
        new = NewPasswordDialog.ask(self.root, "Смена пароля", "Новый мастер-пароль:")
        if not new:
            return
        try:
            self.vault.secrets.change_password(old, new)
        except Exception as exc:
            messagebox.showerror(APP_NAME, "Не удалось сменить пароль: %s" % exc, parent=self.root)
            return
        messagebox.showinfo(APP_NAME, "Мастер-пароль изменён.", parent=self.root)

    # ------------------------------------------------------------------ дерево
    def refresh_tree(self, keep_selection=False):
        if not self.vault:
            return
        selected = self.current_id if keep_selection else None
        self.tree.delete(*self.tree.get_children())
        self.devices = self.vault.search(self.search_var.get())
        if not self.search_var.get().strip():
            self._refresh_peer_values()
        mode = self.group_var.get()

        self._suppress_select = True
        try:
            if mode == "flat":
                for device in self.devices:
                    self._insert_device("", device)
            else:
                groups = {}
                for device in self.devices:
                    if mode == "kind":
                        key = KIND_LABELS.get(device.kind, device.kind)
                    else:
                        key = device.site or "Без площадки"
                    groups.setdefault(key, []).append(device)
                for group in sorted(groups):
                    node = self.tree.insert("", tk.END, iid="group::" + group,
                                            text="%s (%d)" % (group, len(groups[group])),
                                            open=True)
                    for device in groups[group]:
                        self._insert_device(node, device)
            if selected and self.tree.exists(selected):
                self.tree.selection_set(selected)
                self.tree.see(selected)
        finally:
            self._suppress_select = False
        self._update_status()

    def _insert_device(self, parent, device):
        self.tree.insert(parent, tk.END, iid=device.id, text=device.name,
                         values=(device.target or "—", device.kind))

    def on_tree_select(self, _event=None):
        if self._suppress_select:
            return
        selection = self.tree.selection()
        if not selection or selection[0].startswith("group::"):
            return
        device_id = selection[0]
        if device_id == self.current_id:
            return
        if not self._confirm_discard():
            self._suppress_select = True
            try:
                if self.current_id and self.tree.exists(self.current_id):
                    self.tree.selection_set(self.current_id)
            finally:
                self._suppress_select = False
            return
        self.load_device(device_id)

    # --------------------------------------------------------------- карточка
    def load_device(self, device_id):
        device = self.vault.get(device_id)
        if not device:
            return
        self.current_id = device_id
        meta = device.to_meta()
        for key, var in self.field_vars.items():
            value = meta.get(key, "")
            var.set(", ".join(value) if isinstance(value, list) else str(value))
        self._load_ports(device)
        self.note_text.delete("1.0", tk.END)
        self.note_text.insert("1.0", device.body)
        self.note_text.edit_reset()
        self.title_label.config(text="%s — %s" % (device.name, device.target or "без адреса"))
        self.load_secret(device)
        self.refresh_collected()
        self._update_status()

    def reload_current(self):
        if self.current_id:
            self.load_device(self.current_id)

    def _clear_form(self):
        for var in self.field_vars.values():
            var.set("")
        for var in self.secret_vars.values():
            var.set("")
        self.port_groups = []
        self.port_config = {}
        self.device_vlans_var.set("")
        self.plain_uplinks_var.set("")
        self._reset_port_editor()
        self._refresh_port_groups()
        self.note_text.delete("1.0", tk.END)
        self.title_label.config(text="Выберите устройство")
        self.collected_list.delete(0, tk.END)

    def collect_form(self):
        """Собрать устройство из полей формы."""
        values = {key: var.get().strip() for key, var in self.field_vars.items()}
        for key in LIST_FIELDS:
            values[key] = [v.strip() for v in values[key].split(",") if v.strip()]
        values.update(self._collect_ports())
        values["port"] = values["port"] or DEFAULT_PORTS.get(values.get("protocol", "ssh"), 22)
        try:
            values["port"] = int(values["port"])
        except (TypeError, ValueError):
            values["port"] = DEFAULT_PORTS.get(values.get("protocol", "ssh"), 22)
        base = self.vault.get(self.current_id) if self.current_id else None
        if base:
            values["created"] = base.created
            values.update(base.extra)
        device = Device(device_id=self.current_id, body=self.note_text.get("1.0", tk.END).rstrip(),
                        **values)
        return device

    def _is_dirty(self):
        if not self.vault or not self.current_id:
            return False
        stored = self.vault.get(self.current_id)
        if not stored:
            return False
        current = self.collect_form()
        stored_meta = dict(stored.to_meta())
        current_meta = dict(current.to_meta())
        for meta in (stored_meta, current_meta):
            meta.pop("updated", None)
            for key in ("uplinks", "vlans"):
                meta[key] = sorted(meta.get(key) or [])
        return stored_meta != current_meta or stored.body.rstrip() != current.body.rstrip()

    def _confirm_discard(self):
        """True — можно продолжать (изменений нет, сохранили или пользователь отказался)."""
        if not self._is_dirty():
            return True
        answer = messagebox.askyesnocancel(
            APP_NAME, "Есть несохранённые изменения в «%s». Сохранить?" % self.field_vars["name"].get(),
            parent=self.root)
        if answer is None:
            return False
        if answer:
            return self.save_current()
        return True

    def save_current(self):
        if not self.vault:
            return False
        device = self.collect_form()
        if not device.name:
            messagebox.showwarning(APP_NAME, "Укажите название устройства", parent=self.root)
            return False
        problems = device.validate()
        if problems:
            if not messagebox.askyesno(
                    APP_NAME, "Есть замечания:\n\n%s\n\nСохранить всё равно?" % "\n".join(problems),
                    parent=self.root):
                return False
        rename_from = None
        if self.current_id:
            new_id = slugify(device.name)
            if new_id != self.current_id:
                if self.vault.device_path(new_id).exists():
                    new_id = self.vault.unique_id(device.name)
                rename_from = self.current_id
                device.id = new_id
        else:
            device.id = self.vault.unique_id(device.name)
        try:
            self.vault.save(device, rename_from=rename_from)
        except (VaultError, OSError) as exc:
            messagebox.showerror(APP_NAME, "Не удалось сохранить: %s" % exc, parent=self.root)
            return False
        self.current_id = device.id
        self.refresh_tree(keep_selection=True)
        self.title_label.config(text="%s — %s" % (device.name, device.target or "без адреса"))
        self.status_label.config(text="Сохранено: %s" % device.id)
        return True

    def new_device(self):
        if not self.vault or not self._confirm_discard():
            return
        self.current_id = None
        self._clear_form()
        self.field_vars["kind"].set("switch")
        self.field_vars["protocol"].set("ssh")
        self.field_vars["port"].set("22")
        self.field_vars["status"].set("active")
        self.note_text.insert("1.0", "## Назначение\n\n## Что настроено\n\n## История изменений\n")
        self.title_label.config(text="Новое устройство")
        self.notebook.select(0)
        self.tree.selection_remove(self.tree.selection())

    def duplicate_device(self):
        if not self.current_id:
            return
        device = self.vault.get(self.current_id)
        if not device:
            return
        self.current_id = None
        self.field_vars["name"].set(device.name + " (копия)")
        self.field_vars["mgmt_ip"].set("")
        self.field_vars["serial"].set("")
        self.field_vars["secret"].set("")
        self.title_label.config(text="Новое устройство (копия %s)" % device.name)

    def delete_device(self):
        if not self.current_id:
            return
        if not messagebox.askyesno(
                APP_NAME, "Удалить «%s» вместе с сохранёнными доступами?" % self.current_id,
                parent=self.root):
            return
        if not self.ensure_unlocked():
            return
        self.vault.delete(self.current_id)
        self.current_id = None
        self._clear_form()
        self.refresh_tree()

    def open_note_externally(self):
        if not self.current_id:
            return
        self._open_path(self.vault.device_path(self.current_id))

    def open_folder(self):
        if self.vault:
            self._open_path(self.vault.path)

    def _open_path(self, path):
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["cmd", "/c", "start", "", str(path)], shell=False)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror(APP_NAME, "Не удалось открыть: %s" % exc, parent=self.root)

    def _insert_template(self, template):
        from datetime import date
        text = template % date.today().isoformat() if "%s" in template else template
        self.note_text.insert(tk.END, text)
        self.note_text.see(tk.END)

    def _on_protocol_change(self, _event=None):
        protocol = self.field_vars["protocol"].get()
        current_port = self.field_vars["port"].get().strip()
        if not current_port or current_port in [str(p) for p in DEFAULT_PORTS.values()]:
            self.field_vars["port"].set(str(DEFAULT_PORTS.get(protocol, 22)))

    # ----------------------------------------------------------------- порты
    def add_port_group(self):
        """Добавить группу портов по заданным типу, шасси и количеству."""
        try:
            count = int(self.port_count_var.get())
            first = int(self.port_first_var.get())
        except ValueError:
            messagebox.showwarning(APP_NAME, "Количество и номер — числа", parent=self.root)
            return
        if count < 1 or first < 0:
            messagebox.showwarning(APP_NAME, "Количество — от 1, номер — от 0", parent=self.root)
            return
        code = PORT_TYPE_CODES.get(self.port_type_var.get(), self.port_type_var.get())
        spec = ports.make_group(code, self.port_path_var.get(), first, count)
        if len(self._port_order()) + count > ports.MAX_PORTS:
            messagebox.showwarning(
                APP_NAME, "Больше %d портов на устройство не бывает" % ports.MAX_PORTS,
                parent=self.root)
            return
        if spec not in self.port_groups:
            self.port_groups.append(spec)
        self._refresh_port_groups()

    def remove_port_group(self):
        for index in reversed(self.port_group_list.curselection()):
            del self.port_groups[index]
        self._refresh_port_groups()

    def clear_port_groups(self):
        self.port_groups = []
        self._refresh_port_groups()

    def _refresh_port_groups(self):
        self.port_group_list.delete(0, tk.END)
        for spec in self.port_groups:
            self.port_group_list.insert(tk.END, ports.describe_group(spec))
        total = len(self._port_order())
        self.port_total_label.config(text="Всего: %d %s" % (total, ports.plural(total)))
        self.refresh_ports_table()

    def _port_order(self):
        """Все порты по порядку: сначала из групп, затем настроенные вручную."""
        names = ports.expand(self.port_groups)
        known = {name.lower() for name in names}
        names.extend(cfg["name"] for key, cfg in self.port_config.items() if key not in known)
        return names

    def _port_cfg(self, name, create=True):
        cfg = self.port_config.get(name.lower())
        if cfg is None and create:
            cfg = {"name": name, "mode": "", "vlans": [], "peer": "", "peer_port": ""}
            self.port_config[name.lower()] = cfg
        return cfg

    @staticmethod
    def _is_set(cfg):
        return bool(cfg and (cfg["mode"] or cfg["vlans"] or cfg["peer"]))

    def refresh_ports_table(self):
        selected = set(self.ports_tree.selection())
        self.ports_tree.delete(*self.ports_tree.get_children())
        from_groups = {name.lower() for name in ports.expand(self.port_groups)}
        for name in self._port_order():
            cfg = self._port_cfg(name, create=False)
            if self.only_configured.get() and not self._is_set(cfg):
                continue
            tags = []
            if self._is_set(cfg):
                tags.append("set")
            if name.lower() not in from_groups:
                tags.append("extra")
            self.ports_tree.insert(
                "", tk.END, iid=name, text=name, tags=tags,
                values=(cfg["mode"] if cfg else "",
                        " ".join(cfg["vlans"]) if cfg else "",
                        cfg["peer"] if cfg else "",
                        cfg["peer_port"] if cfg else ""))
        keep = [name for name in selected if self.ports_tree.exists(name)]
        if keep:
            self.ports_tree.selection_set(*keep)

    def on_port_select(self, _event=None):
        selection = self.ports_tree.selection()
        if not selection:
            self.port_selection_label.config(text="Порт не выбран")
            return
        if len(selection) == 1:
            self.port_selection_label.config(text="Порт %s" % selection[0])
        else:
            self.port_selection_label.config(
                text="Выбрано %d %s: %s … %s" % (len(selection), ports.plural(len(selection)),
                                                 selection[0], selection[-1]))
        single = len(selection) == 1
        cfg = self._port_cfg(selection[0], create=False) or {}
        self.port_mode_var.set(cfg.get("mode", ""))
        self.port_vlans_var.set(" ".join(cfg.get("vlans", [])))
        self.port_peer_var.set(cfg.get("peer", "") if single else "")
        self.port_peer_port_var.set(cfg.get("peer_port", "") if single else "")
        self.peer_combo.config(state="normal" if single else "disabled")
        self.peer_port_entry.config(state="normal" if single else "disabled")

    def apply_port_settings(self):
        selection = self.ports_tree.selection()
        if not selection:
            messagebox.showinfo(APP_NAME, "Сначала выберите порт в таблице", parent=self.root)
            return
        mode = self.port_mode_var.get().strip()
        vlans = self.port_vlans_var.get().replace(",", " ").split()
        # Режим и VLAN у пачки портов одинаковые, а аплинк у каждого свой —
        # поэтому сосед и его порт меняются только при выборе одного порта.
        single = len(selection) == 1
        for name in selection:
            cfg = self._port_cfg(name)
            cfg["mode"], cfg["vlans"] = mode, list(vlans)
            if single:
                cfg["peer"] = self.port_peer_var.get().strip()
                cfg["peer_port"] = self.port_peer_port_var.get().strip()
            if not self._is_set(cfg):
                self.port_config.pop(name.lower(), None)
        self.refresh_ports_table()

    def clear_port_settings(self):
        for name in self.ports_tree.selection():
            self.port_config.pop(name.lower(), None)
        self.port_mode_var.set("")
        self.port_vlans_var.set("")
        self.port_peer_var.set("")
        self.port_peer_port_var.set("")
        self.refresh_ports_table()

    def _load_ports(self, device):
        """Разложить поля ports/uplinks/vlans заметки по портам."""
        self.port_groups = list(device.ports)
        self.port_config = {}
        plain, device_vlans = [], []
        for entry in device.uplinks:
            local_port, peer, peer_port = links.parse(entry)
            if not peer:
                continue
            if local_port:
                cfg = self._port_cfg(local_port)
                cfg["peer"], cfg["peer_port"] = peer, peer_port
            else:
                plain.append(links.format("", peer, peer_port))
        for entry in device.vlans:
            port, mode, vlans = ports.parse_vlan(entry)
            if port:
                cfg = self._port_cfg(port)
                cfg["mode"], cfg["vlans"] = mode, vlans
            else:
                device_vlans.extend(vlans)
        self.plain_uplinks_var.set(", ".join(plain))
        self.device_vlans_var.set(", ".join(device_vlans))
        self._reset_port_editor()
        self._refresh_port_groups()

    def _reset_port_editor(self):
        self.port_mode_var.set("")
        self.port_vlans_var.set("")
        self.port_peer_var.set("")
        self.port_peer_port_var.set("")
        self.port_selection_label.config(text="Порт не выбран")

    def _collect_ports(self):
        """Собрать из настроек портов поля ports, uplinks и vlans."""
        uplinks, vlans = [], []
        vlans.extend(v.strip() for v in self.device_vlans_var.get().split(",") if v.strip())
        for name in self._port_order():
            cfg = self._port_cfg(name, create=False)
            if not cfg:
                continue
            if cfg["peer"]:
                uplinks.append(links.format(name, cfg["peer"], cfg["peer_port"]))
            entry = ports.format_vlan(name, cfg["mode"], cfg["vlans"])
            if entry:
                vlans.append(entry)
        uplinks.extend(p.strip() for p in self.plain_uplinks_var.get().split(",") if p.strip())
        return {"ports": list(self.port_groups), "uplinks": uplinks, "vlans": vlans}

    def _refresh_peer_values(self):
        self.peer_combo["values"] = sorted(device.id for device in self.devices)

    # --------------------------------------------------------------- доступы
    def load_secret(self, device):
        for var in self.secret_vars.values():
            var.set("")
        if not self.vault or self.vault.is_locked:
            self.secret_hint.config(text="Хранилище закрыто — нажмите «Сохранить доступы» "
                                         "или Ctrl+L/пароль, чтобы открыть.")
            return
        record = self.vault.secrets.get(device.secret_ref) or {}
        for key in SECRET_FIELDS:
            self.secret_vars[key].set(record.get(key, ""))
        self.secret_hint.config(
            text="Запись: %s%s" % (device.secret_ref,
                                   " (обновлена %s)" % record["updated"] if record.get("updated") else " — пусто"))

    def save_secret(self):
        if not self.current_id and not self.field_vars["name"].get().strip():
            return
        if not self.ensure_unlocked():
            return
        if not self.current_id:
            if not self.save_current():
                return
        device = self.vault.get(self.current_id)
        values = {key: self.secret_vars[key].get() for key in SECRET_FIELDS}
        try:
            self.vault.secrets.put(device.secret_ref, **values)
        except LockedError:
            return
        self.vault.log(device.id, "secret_updated", {})
        self.secret_hint.config(text="Доступы сохранены: %s" % device.secret_ref)

    def delete_secret(self):
        if not self.current_id or not self.ensure_unlocked():
            return
        device = self.vault.get(self.current_id)
        if messagebox.askyesno(APP_NAME, "Удалить доступы для %s?" % device.secret_ref,
                               parent=self.root):
            self.vault.secrets.delete(device.secret_ref)
            for var in self.secret_vars.values():
                var.set("")
            self.secret_hint.config(text="Доступы удалены")

    def _toggle_secret_visibility(self):
        show = "" if self.show_secrets.get() else "•"
        for key, entry in self.secret_entries.items():
            if key in ("private_key_path", "notes", "username"):
                continue
            entry.config(show=show)

    def generate_password(self):
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
        password = "".join(pysecrets.choice(alphabet) for _ in range(20))
        self.secret_vars["password"].set(password)
        self.show_secrets.set(True)
        self._toggle_secret_visibility()
        self.secret_hint.config(text="Сгенерирован пароль — не забудьте «Сохранить доступы»")

    def copy_secret(self, key):
        if not self.ensure_unlocked():
            return
        value = self.secret_vars[key].get()
        if not value:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        seconds = int(self.vault.config.get("clipboard_clear_seconds", 30)) if self.vault else 30
        self._clipboard_token += 1
        token = self._clipboard_token
        self.root.after(seconds * 1000, lambda: self._clear_clipboard(value, token))
        self.status_label.config(text="Скопировано в буфер (очистится через %d с)" % seconds)

    def _clear_clipboard(self, value, token):
        if token != self._clipboard_token:
            return
        try:
            if self.root.clipboard_get() == value:
                self.root.clipboard_clear()
                self.status_label.config(text="Буфер обмена очищен")
        except tk.TclError:
            pass

    # ------------------------------------------------------------- собранное
    def refresh_collected(self):
        self.collected_list.delete(0, tk.END)
        self._collected_paths = []
        if not (self.vault and self.current_id):
            return
        for path in self.vault.device_notes(self.current_id):
            self._collected_paths.append(path)
            self.collected_list.insert(tk.END, path.name)

    def show_collected(self, _event=None):
        selection = self.collected_list.curselection()
        if not selection:
            return
        path = self._collected_paths[selection[0]]
        self.collected_text.config(state=tk.NORMAL)
        self.collected_text.delete("1.0", tk.END)
        try:
            self.collected_text.insert("1.0", path.read_text(encoding="utf-8"))
        except OSError as exc:
            self.collected_text.insert("1.0", "Не удалось прочитать: %s" % exc)
        self.collected_text.config(state=tk.DISABLED)

    # ------------------------------------------------------------- сервисные
    def export(self, fmt):
        if not self.vault:
            return
        filename = filedialog.asksaveasfilename(
            parent=self.root, defaultextension="." + fmt,
            filetypes=[(fmt.upper(), "*." + fmt)], initialfile="inventory." + fmt)
        if not filename:
            return
        import csv
        import json
        devices = self.vault.devices()
        with open(filename, "w", encoding="utf-8-sig", newline="") as fh:
            if fmt == "json":
                json.dump([dict(d.to_meta(), id=d.id) for d in devices], fh,
                          ensure_ascii=False, indent=2)
            else:
                columns = ["id", "name", "kind", "mgmt_ip", "hostname", "vendor", "model",
                           "site", "rack", "role", "status", "tags"]
                writer = csv.writer(fh, delimiter=";")
                writer.writerow(columns)
                for device in devices:
                    meta = dict(device.to_meta(), id=device.id)
                    writer.writerow([", ".join(meta[c]) if isinstance(meta.get(c), list)
                                     else meta.get(c, "") for c in columns])
        messagebox.showinfo(APP_NAME, "Выгружено без паролей: %s" % filename, parent=self.root)

    def show_doctor(self):
        if not self.vault:
            return
        stats = self.vault.stats()
        lines = ["Устройств: %d" % stats["total"], ""]
        lines.append("По типам: " + ", ".join("%s=%d" % kv for kv in stats["by_kind"].items()))
        lines.append("По площадкам: " + ", ".join("%s=%d" % kv for kv in stats["by_site"].items()))
        ips = {}
        for device in self.vault.devices():
            if device.mgmt_ip:
                ips.setdefault(device.mgmt_ip, []).append(device.id)
        duplicates = ["%s: %s" % (ip, ", ".join(ids)) for ip, ids in ips.items() if len(ids) > 1]
        if duplicates:
            lines += ["", "Дубликаты IP:"] + duplicates
        if stats["problems"]:
            lines += ["", "Замечания:"] + ["%s: %s" % (k, "; ".join(v))
                                           for k, v in stats["problems"].items()]
        missing = ["%s → %s" % (a, b) for a, b, found in self.vault.topology() if not found]
        if missing:
            lines += ["", "Аплинки на неизвестные устройства:"] + missing
        self._show_text_window("Проверка хранилища", "\n".join(lines))

    def show_topology(self):
        if not self.vault:
            return
        edges = self.vault.topology()
        if not edges:
            self._show_text_window("Топология", "Аплинки не заданы ни у одного устройства.")
            return
        children = {}
        for source, target, _found in edges:
            children.setdefault(target, []).append(source)
        lines = []
        for target in sorted(children):
            lines.append(target)
            for source in sorted(children[target]):
                lines.append("    └── %s" % source)
            lines.append("")
        self._show_text_window("Топология (кто через кого включён)", "\n".join(lines))

    def _show_text_window(self, title, text):
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("640x480")
        widget = tk.Text(window, wrap=tk.NONE, font=("Consolas", 10))
        widget.pack(fill=tk.BOTH, expand=True)
        widget.insert("1.0", text)
        widget.config(state=tk.DISABLED)

    def launch_netmaster(self, device_id=None):
        """Открыть NetMaster: собранную программу рядом или скрипт из исходников."""
        found = launcher.command_for("NetMaster", "netmaster/main.py")
        if found is None:
            messagebox.showerror(APP_NAME, launcher.not_found_message("NetMaster"),
                                 parent=self.root)
            return
        command, work_dir = found
        if self.vault:
            command += ["--vault", str(self.vault.path)]
        if device_id:
            command += ["--device", device_id]
        try:
            subprocess.Popen(command, cwd=work_dir)
            self.status_label.config(text="NetMaster запущен")
        except OSError as exc:
            messagebox.showerror(APP_NAME, "Не удалось запустить NetMaster: %s" % exc,
                                 parent=self.root)

    def launch_netmap(self):
        """Открыть карту сети: соседнюю программу NetMap либо её скрипт."""
        found = launcher.command_for("NetMap", "netweb/main.py")
        if found is None:
            messagebox.showerror(APP_NAME, launcher.not_found_message("NetMap"),
                                 parent=self.root)
            return
        command, work_dir = found
        if self.vault:
            command += ["--vault", str(self.vault.path)]
        try:
            subprocess.Popen(command, cwd=work_dir)
            self.status_label.config(text="Карта сети открывается в браузере")
        except OSError as exc:
            messagebox.showerror(APP_NAME, "Не удалось открыть карту: %s" % exc,
                                 parent=self.root)

    def launch_netmaster_for_device(self):
        self.launch_netmaster(self.current_id)

    def show_about(self):
        messagebox.showinfo(
            APP_NAME,
            "%s %s\n\nХранилище данных о серверах, свитчах и роутерах.\n"
            "Заметки — Markdown (совместимо с Obsidian),\n"
            "пароли — AES-256-GCM в secrets.enc.\n\n"
            "Управление устройствами — приложение NetMaster." % (APP_NAME, __version__),
            parent=self.root)

    # ---------------------------------------------------------------- статус
    def _tick(self):
        """Раз в 15 секунд: автоблокировка и обновление статуса."""
        if self.vault and not self.vault.is_locked:
            if self.vault.secrets.maybe_autolock(self.vault.autolock_seconds()):
                for var in self.secret_vars.values():
                    var.set("")
                self.status_label.config(text="Хранилище заблокировано по бездействию")
        self._update_status()
        self.root.after(15000, self._tick)

    def _update_status(self):
        if not self.vault:
            self.lock_label.config(text="Хранилище не открыто", style="Locked.TLabel")
            return
        if self.vault.is_locked:
            self.lock_label.config(text="🔒 закрыто — %s" % self.vault.path, style="Locked.TLabel")
        else:
            minutes = int(self.vault.config.get("autolock_minutes", 10))
            self.lock_label.config(
                text="🔓 открыто (автоблокировка %d мин) — %s" % (minutes, self.vault.path),
                style="Unlocked.TLabel")
        if not self.status_label.cget("text") or self.devices is not None:
            count = len(self.devices)
            total = len(list((self.vault.path / "devices").glob("*.md"))) if self.vault.is_vault else 0
            suffix = "" if count == total else " из %d" % total
            self.status_label.config(text="Показано устройств: %d%s" % (count, suffix))

    def on_close(self):
        if not self._confirm_discard():
            return
        if self.vault:
            self.vault.lock()
        self.root.destroy()

"""
Диалог массового выполнения команд: выбрать устройства, ввести команды,
посмотреть результат, при желании сохранить вывод в хранилище.
"""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from netmaster.core import macros, runner


class BulkDialog(tk.Toplevel):
    """Окно массовых операций."""

    def __init__(self, parent, inventory, targets, macros_items=None,
                 title="Массовое выполнение команд"):
        super().__init__(parent)
        self.title(title)
        self.geometry("900x640")
        self.inventory = inventory
        self.targets = list(targets)
        self.macros = macros_items if macros_items is not None else macros.load()
        self.results = []
        self.stop_event = threading.Event()
        self.save_var = tk.BooleanVar(value=True)
        self.enable_var = tk.BooleanVar(value=False)
        self.workers_var = tk.IntVar(value=8)

        self._build_ui()
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Устройств выбрано: %d" % len(self.targets),
                  font=("Arial", 10, "bold")).pack(side=tk.LEFT)
        without = self.inventory.missing_credentials(self.targets)
        if without:
            ttk.Label(top, text="без доступов: %s" % ", ".join(t.id for t in without),
                      foreground="#b00020").pack(side=tk.LEFT, padx=10)

        preset_frame = ttk.Frame(self, padding=(8, 0))
        preset_frame.pack(fill=tk.X)
        ttk.Label(preset_frame, text="Шаблон:").pack(side=tk.LEFT)
        self.preset = ttk.Combobox(preset_frame, values=[self._label(m) for m in self.macros],
                                   state="readonly", width=32)
        self.preset.pack(side=tk.LEFT, padx=4)
        self.preset.bind("<<ComboboxSelected>>", self._apply_preset)

        ttk.Checkbutton(preset_frame, text="войти в enable",
                        variable=self.enable_var).pack(side=tk.LEFT, padx=8)
        ttk.Checkbutton(preset_frame, text="сохранить вывод в хранилище",
                        variable=self.save_var).pack(side=tk.LEFT)
        ttk.Label(preset_frame, text="потоков:").pack(side=tk.LEFT, padx=(8, 2))
        ttk.Spinbox(preset_frame, from_=1, to=32, width=4,
                    textvariable=self.workers_var).pack(side=tk.LEFT)

        ttk.Label(self, text="Команды (по одной в строке):",
                  padding=(8, 6, 8, 0)).pack(anchor=tk.W)
        self.commands = tk.Text(self, height=6, font=("Consolas", 10))
        self.commands.pack(fill=tk.X, padx=8)

        buttons = ttk.Frame(self, padding=8)
        buttons.pack(fill=tk.X)
        self.run_button = ttk.Button(buttons, text="Выполнить", command=self.run)
        self.run_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(buttons, text="Остановить", command=self.stop,
                                      state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Закрыть", command=self._close).pack(side=tk.RIGHT)
        self.progress = ttk.Progressbar(buttons, mode="determinate", length=260)
        self.progress.pack(side=tk.RIGHT, padx=8)

        panes = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        left = ttk.Frame(panes)
        panes.add(left, weight=1)
        self.result_list = tk.Listbox(left, width=34)
        self.result_list.pack(fill=tk.BOTH, expand=True)
        self.result_list.bind("<<ListboxSelect>>", self._show_result)

        right = ttk.Frame(panes)
        panes.add(right, weight=3)
        self.result_text = tk.Text(right, bg="#1e1e1e", fg="#d4d4d4",
                                   font=("Consolas", 9), wrap=tk.NONE)
        self.result_text.pack(fill=tk.BOTH, expand=True)
        self.result_text.config(state=tk.DISABLED)

    @staticmethod
    def _label(item):
        return "%s: %s" % (item["vendor"] or "любой", item["name"])

    def _apply_preset(self, _event=None):
        chosen = self.preset.current()
        if chosen < 0:
            return
        self.commands.delete("1.0", tk.END)
        self.commands.insert("1.0", macros.as_text(self.macros[chosen]))

    # ---------------------------------------------------------------- запуск
    def run(self):
        commands = [line.strip() for line in self.commands.get("1.0", tk.END).splitlines()
                    if line.strip()]
        if not commands:
            messagebox.showwarning("NetMaster", "Введите хотя бы одну команду", parent=self)
            return
        runnable = [t for t in self.targets if t.host and t.has_credentials]
        if not runnable:
            messagebox.showwarning("NetMaster", "Нет устройств с сохранёнными доступами",
                                   parent=self)
            return
        if len(runnable) > 1 and not messagebox.askyesno(
                "NetMaster",
                "Выполнить %d команд(ы) на %d устройствах?" % (len(commands), len(runnable)),
                parent=self):
            return

        self.results = []
        self.result_list.delete(0, tk.END)
        self.stop_event.clear()
        self.progress.config(maximum=len(runnable), value=0)
        self.run_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)

        def progress(done, total, result):
            self.after(0, lambda: self._on_result(done, total, result))

        def work():
            results = runner.run_on_many(
                runnable, commands, workers=self.workers_var.get(), progress=progress,
                stop_event=self.stop_event, use_enable=self.enable_var.get())
            self.after(0, lambda: self._finish(results, commands))

        threading.Thread(target=work, daemon=True).start()

    def _on_result(self, done, total, result):
        self.progress.config(value=done)
        self.results.append(result)
        mark = "OK " if result.ok else "!! "
        self.result_list.insert(tk.END, "%s%s (%s)" % (mark, result.name, result.duration))

    def _finish(self, results, commands):
        self.run_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.results = results
        summary = runner.summarize(results)
        if self.save_var.get():
            title = commands[0].replace(" ", "-")[:40] or "output"
            saved = runner.save_results(self.inventory, results, title)
            summary += "\n\nСохранено в хранилище файлов: %d" % len(saved)
        self._set_text(summary)

    def stop(self):
        self.stop_event.set()
        self.stop_button.config(state=tk.DISABLED)

    def _show_result(self, _event=None):
        selection = self.result_list.curselection()
        if not selection or selection[0] >= len(self.results):
            return
        result = self.results[selection[0]]
        text = result.error and ("Ошибка: %s\n\n" % result.error) or ""
        self._set_text(text + result.output)

    def _set_text(self, text):
        self.result_text.config(state=tk.NORMAL)
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", text)
        self.result_text.config(state=tk.DISABLED)

    def _close(self):
        self.stop_event.set()
        self.destroy()

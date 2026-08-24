"""Сетевые утилиты в одном окне: ping, traceroute, сканер портов, подсети, DNS."""

import threading
import tkinter as tk
from tkinter import ttk

from netmaster.core import network_tools


class ToolsDialog(tk.Toplevel):
    def __init__(self, parent, host=""):
        super().__init__(parent)
        self.title("Сетевые утилиты")
        self.geometry("760x520")
        self.host_var = tk.StringVar(value=host)
        self.ports_var = tk.StringVar(value="22,23,80,161,443,8080")
        self.network_var = tk.StringVar(value="10.0.0.0/24")

        top = ttk.Frame(self, padding=8)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Хост / IP:").pack(side=tk.LEFT)
        ttk.Entry(top, textvariable=self.host_var, width=28).pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="Ping", command=self.run_ping).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Traceroute", command=self.run_trace).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="DNS", command=self.run_dns).pack(side=tk.LEFT, padx=2)

        second = ttk.Frame(self, padding=(8, 0))
        second.pack(fill=tk.X)
        ttk.Label(second, text="Порты:").pack(side=tk.LEFT)
        ttk.Entry(second, textvariable=self.ports_var, width=28).pack(side=tk.LEFT, padx=6)
        ttk.Button(second, text="Сканировать", command=self.run_scan).pack(side=tk.LEFT, padx=2)
        ttk.Label(second, text="Сеть:").pack(side=tk.LEFT, padx=(16, 0))
        ttk.Entry(second, textvariable=self.network_var, width=18).pack(side=tk.LEFT, padx=6)
        ttk.Button(second, text="Подсеть", command=self.run_subnet).pack(side=tk.LEFT, padx=2)

        self.output = tk.Text(self, bg="#1e1e1e", fg="#d4d4d4", font=("Consolas", 10))
        self.output.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.output.config(state=tk.DISABLED)
        self.transient(parent)

    # ------------------------------------------------------------- запуск
    def _run_async(self, label, func):
        self._write("\n=== %s ===\n" % label)
        def work():
            try:
                result = func()
            except Exception as exc:
                result = "Ошибка: %s" % exc
            self.after(0, lambda: self._write(self._format(result)))
        threading.Thread(target=work, daemon=True).start()

    def _format(self, result):
        if isinstance(result, dict):
            if "output" in result:
                return str(result["output"]).rstrip() + "\n"
            return "\n".join("%s: %s" % (k, v) for k, v in result.items()) + "\n"
        return str(result) + "\n"

    def run_ping(self):
        host = self.host_var.get().strip()
        self._run_async("ping %s" % host, lambda: network_tools.PingTool().ping(host))

    def run_trace(self):
        host = self.host_var.get().strip()
        self._run_async("traceroute %s" % host, lambda: network_tools.TracerouteTool().trace(host))

    def run_dns(self):
        host = self.host_var.get().strip()
        self._run_async("dns %s" % host, lambda: network_tools.DNSLookup().lookup(host))

    def run_scan(self):
        host = self.host_var.get().strip()
        ports = [int(p) for p in self.ports_var.get().replace(" ", "").split(",") if p.isdigit()]
        self._run_async("scan %s" % host,
                        lambda: network_tools.PortScanner().scan(host, ports))

    def run_subnet(self):
        network = self.network_var.get().strip()
        self._run_async("subnet %s" % network,
                        lambda: network_tools.SubnetCalculator().calculate(network))

    def _write(self, text):
        self.output.config(state=tk.NORMAL)
        self.output.insert(tk.END, text)
        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)

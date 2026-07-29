"""
Main window module for NetMaster application.
Provides the primary GUI interface with tabbed terminal sessions,
connection manager, and network tools.

Designed for Ali - Network Engineer
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import os
from pathlib import Path

class MainWindow:
    """Main application window with tabbed interface."""
    
    def __init__(self, root):
        self.root = root
        self.sessions = {}  # Store active sessions
        self.config_file = Path.home() / ".netmaster" / "connections.json"
        
        # Setup UI
        self._setup_styles()
        self._create_menu()
        self._create_main_layout()
        self._create_status_bar()
        self._load_connections()
        
        print("NetMaster GUI initialized for Ali")
    
    def _setup_styles(self):
        """Configure application styles."""
        style = ttk.Style()
        
        # Try to use modern theme
        available_themes = style.theme_names()
        if 'clam' in available_themes:
            style.theme_use('clam')
        elif 'alt' in available_themes:
            style.theme_use('alt')
        
        # Configure custom colors
        style.configure('Terminal.TFrame', background='#1e1e1e')
        style.configure('Connected.TLabel', foreground='#4CAF50', font=('Arial', 9, 'bold'))
        style.configure('Disconnected.TLabel', foreground='#f44336', font=('Arial', 9))
    
    def _create_menu(self):
        """Create application menu bar."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="New SSH Session", command=self._new_ssh_session, accelerator="Ctrl+T")
        file_menu.add_command(label="New Telnet Session", command=self._new_telnet_session)
        file_menu.add_separator()
        file_menu.add_command(label="Save Configuration", command=self._save_config)
        file_menu.add_command(label="Exit", command=self.root.quit)
        
        # Connections menu
        conn_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Connections", menu=conn_menu)
        conn_menu.add_command(label="Connection Manager", command=self._open_connection_manager)
        conn_menu.add_command(label="Quick Connect", command=self._quick_connect)
        
        # Tools menu
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(label="Ping Tool", command=self._open_ping_tool)
        tools_menu.add_command(label="Traceroute", command=self._open_traceroute_tool)
        tools_menu.add_command(label="Port Scanner", command=self._open_port_scanner)
        tools_menu.add_command(label="Subnet Calculator", command=self._open_subnet_calculator)
        
        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About NetMaster", command=self._show_about)
        
        # Keyboard shortcuts
        self.root.bind('<Control-t>', lambda e: self._new_ssh_session())
    
    def _create_main_layout(self):
        """Create main application layout with paned windows."""
        # Main paned window (horizontal)
        self.main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self.main_paned.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        # Left panel - Connection tree and quick access
        left_frame = ttk.Frame(self.main_paned, width=250)
        self.main_paned.add(left_frame, weight=1)
        
        # Connection tree
        tree_frame = ttk.LabelFrame(left_frame, text="Saved Connections", padding=5)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.connection_tree = ttk.Treeview(tree_frame, show='tree')
        self.connection_tree.pack(fill=tk.BOTH, expand=True)
        
        # Tree context menu
        self.tree_menu = tk.Menu(self.root, tearoff=0)
        self.tree_menu.add_command(label="Connect", command=self._tree_connect)
        self.tree_menu.add_command(label="Edit", command=self._tree_edit)
        self.tree_menu.add_command(label="Delete", command=self._tree_delete)
        self.connection_tree.bind('<Button-3>', self._show_tree_menu)
        self.connection_tree.bind('<Double-1>', lambda e: self._tree_connect())
        
        # Buttons for left panel
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Button(btn_frame, text="+ Add", command=self._add_connection).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="↻ Refresh", command=self._refresh_tree).pack(side=tk.LEFT, padx=2)
        
        # Center panel - Terminal tabs
        center_frame = ttk.Frame(self.main_paned)
        self.main_paned.add(center_frame, weight=3)
        
        # Notebook for terminal tabs
        self.notebook = ttk.Notebook(center_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Welcome tab
        welcome_frame = ttk.Frame(self.notebook)
        self.notebook.add(welcome_frame, text="Welcome")
        
        welcome_label = ttk.Label(
            welcome_frame, 
            text="Welcome to NetMaster, Ali!\n\n"
                 "Click '+' to start a new session or select from saved connections.",
            font=('Arial', 14),
            justify=tk.CENTER
        )
        welcome_label.pack(expand=True)
        
        # Tab bar with new button
        tab_bar_frame = ttk.Frame(self.notebook)
        ttk.Button(tab_bar_frame, text="+", width=3, command=self._new_ssh_session).pack(side=tk.RIGHT)
        self.notebook.bind('<Button-3>', lambda e: self._show_tab_menu(e))
    
    def _create_status_bar(self):
        """Create status bar at bottom."""
        self.status_bar = ttk.Label(
            self.root, 
            text="Ready | Sessions: 0 | Mode: Normal",
            relief=tk.SUNKEN,
            anchor=tk.W
        )
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
    
    def _load_connections(self):
        """Load saved connections from config file."""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r') as f:
                    self.connections = json.load(f)
                self._refresh_tree()
                print(f"Loaded {len(self.connections)} connections for Ali")
            else:
                self.connections = []
                # Create default directory
                self.config_file.parent.mkdir(parents=True, exist_ok=True)
                self._save_config()
        except Exception as e:
            print(f"Error loading connections: {e}")
            self.connections = []
    
    def _save_config(self):
        """Save connections to config file."""
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(self.connections, f, indent=2)
            messagebox.showinfo("Success", "Configuration saved successfully, Ali!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save configuration: {e}")
    
    def _refresh_tree(self):
        """Refresh connection tree view."""
        # Clear existing items
        for item in self.connection_tree.get_children():
            self.connection_tree.delete(item)
        
        # Group by categories if needed
        for conn in self.connections:
            name = conn.get('name', 'Unnamed')
            host = conn.get('host', '')
            port = conn.get('port', 22)
            conn_type = conn.get('type', 'SSH')
            
            # Create tree item
            item_id = self.connection_tree.insert('', tk.END, text=name, values=(host,))
            self.connection_tree.item(item_id, tags=(conn_type.lower(),))
    
    def _new_ssh_session(self):
        """Create new SSH session tab."""
        from gui.terminal_widget import TerminalWidget
        
        tab_id = len(self.sessions) + 1
        tab_name = f"SSH-{tab_id}"
        
        # Create terminal widget
        terminal = TerminalWidget(self.notebook, session_id=tab_id)
        self.notebook.add(terminal, text=tab_name)
        self.notebook.select(terminal)
        
        self.sessions[tab_id] = {
            'type': 'SSH',
            'widget': terminal,
            'connected': False
        }
        
        self._update_status()
        print(f"New SSH session created for Ali: {tab_name}")
    
    def _new_telnet_session(self):
        """Create new Telnet session tab."""
        # Placeholder for Telnet implementation
        messagebox.showinfo("Info", "Telnet support coming soon, Ali!")
    
    def _open_connection_manager(self):
        """Open connection manager dialog."""
        from gui.connection_dialog import ConnectionDialog
        dialog = ConnectionDialog(self.root, self.connections, self._on_connection_saved)
    
    def _on_connection_saved(self, connections):
        """Callback when connection is saved."""
        self.connections = connections
        self._refresh_tree()
        self._save_config()
    
    def _add_connection(self):
        """Add new connection via dialog."""
        self._open_connection_manager()
    
    def _quick_connect(self):
        """Quick connect dialog."""
        # Simple quick connect
        top = tk.Toplevel(self.root)
        top.title("Quick Connect - NetMaster")
        top.geometry("400x200")
        
        ttk.Label(top, text="Host:").pack(pady=5)
        host_entry = ttk.Entry(top, width=40)
        host_entry.pack(pady=5)
        
        ttk.Label(top, text="Username:").pack(pady=5)
        user_entry = ttk.Entry(top, width=40)
        user_entry.pack(pady=5)
        
        def connect():
            host = host_entry.get()
            if host:
                self._connect_to_host(host, user_entry.get())
                top.destroy()
        
        ttk.Button(top, text="Connect", command=connect).pack(pady=10)
    
    def _connect_to_host(self, host, username=''):
        """Connect to specified host."""
        self._new_ssh_session()
        # Will be implemented with paramiko integration
        print(f"Connecting to {host} as {username} for Ali")
    
    def _show_tree_menu(self, event):
        """Show context menu for connection tree."""
        item = self.connection_tree.identify_row(event.y)
        if item:
            self.connection_tree.selection_set(item)
            self.tree_menu.post(event.x_root, event.y_root)
    
    def _tree_connect(self):
        """Connect from tree selection."""
        selection = self.connection_tree.selection()
        if selection:
            item = selection[0]
            name = self.connection_tree.item(item, 'text')
            # Find connection details
            for conn in self.connections:
                if conn.get('name') == name:
                    self._connect_to_host(conn.get('host'), conn.get('username', ''))
                    break
    
    def _tree_edit(self):
        """Edit selected connection."""
        # Implementation pending
        messagebox.showinfo("Info", "Edit feature coming soon, Ali!")
    
    def _tree_delete(self):
        """Delete selected connection."""
        selection = self.connection_tree.selection()
        if selection:
            item = selection[0]
            name = self.connection_tree.item(item, 'text')
            if messagebox.askyesno("Confirm", f"Delete connection '{name}'?"):
                self.connections = [c for c in self.connections if c.get('name') != name]
                self._refresh_tree()
                self._save_config()
    
    def _show_tab_menu(self, event):
        """Show tab context menu."""
        # Identify clicked tab
        pass
    
    def _update_status(self):
        """Update status bar."""
        active_sessions = sum(1 for s in self.sessions.values() if s.get('connected', False))
        total_sessions = len(self.sessions)
        self.status_bar.config(text=f"Ready | Sessions: {total_sessions} ({active_sessions} active) | NetMaster for Ali")
    
    # Tool placeholders
    def _open_ping_tool(self):
        messagebox.showinfo("Tools", "Ping tool interface coming soon, Ali!")
    
    def _open_traceroute_tool(self):
        messagebox.showinfo("Tools", "Traceroute tool coming soon, Ali!")
    
    def _open_port_scanner(self):
        messagebox.showinfo("Tools", "Port scanner coming soon, Ali!")
    
    def _open_subnet_calculator(self):
        messagebox.showinfo("Tools", "Subnet calculator coming soon, Ali!")
    
    def _show_about(self):
        """Show about dialog."""
        messagebox.showinfo(
            "About NetMaster",
            "NetMaster v0.1\n\n"
            "Professional Network Engineer Tool\n"
            "Created for Ali\n\n"
            "Features:\n"
            "- Multi-session SSH/Telnet\n"
            "- Connection Manager\n"
            "- Network Tools\n"
            "- Cross-platform (Windows/Linux)"
        )

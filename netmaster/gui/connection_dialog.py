"""
Connection dialog module for NetMaster.
Provides UI for managing saved connections.

For Ali - Network Engineer
"""

import tkinter as tk
from tkinter import ttk, messagebox

class ConnectionDialog:
    """Dialog for adding/editing connections."""
    
    def __init__(self, parent, connections, on_save_callback):
        self.parent = parent
        self.connections = connections.copy() if connections else []
        self.on_save_callback = on_save_callback
        
        self.top = tk.Toplevel(parent)
        self.top.title("Connection Manager - NetMaster")
        self.top.geometry("700x500")
        self.top.transient(parent)
        self.top.grab_set()
        
        self._setup_ui()
        self._load_connections_list()
    
    def _setup_ui(self):
        """Setup dialog UI."""
        # Main paned window
        paned = ttk.PanedWindow(self.top, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Left panel - Connection list
        left_frame = ttk.Frame(paned, width=300)
        paned.add(left_frame, weight=1)
        
        # Listbox with scrollbar
        list_frame = ttk.LabelFrame(left_frame, text="Saved Connections", padding=5)
        list_frame.pack(fill=tk.BOTH, expand=True)
        
        self.conn_listbox = tk.Listbox(list_frame, font=('Arial', 10))
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.conn_listbox.yview)
        self.conn_listbox.configure(yscrollcommand=scrollbar.set)
        
        self.conn_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.conn_listbox.bind('<<ListboxSelect>>', self._on_selection)
        
        # List buttons
        list_btn_frame = ttk.Frame(left_frame)
        list_btn_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(list_btn_frame, text="New", command=self._new_connection).pack(side=tk.LEFT, padx=2)
        ttk.Button(list_btn_frame, text="Edit", command=self._edit_connection).pack(side=tk.LEFT, padx=2)
        ttk.Button(list_btn_frame, text="Delete", command=self._delete_connection).pack(side=tk.LEFT, padx=2)
        
        # Right panel - Connection details
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=2)
        
        details_frame = ttk.LabelFrame(right_frame, text="Connection Details", padding=10)
        details_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Form fields
        form_fields = [
            ("Name:", "name"),
            ("Host/IP:", "host"),
            ("Port:", "port"),
            ("Username:", "username"),
            ("Password:", "password"),
            ("Description:", "description")
        ]
        
        self.entries = {}
        for i, (label, key) in enumerate(form_fields):
            ttk.Label(details_frame, text=label).grid(row=i, column=0, sticky='w', pady=5)
            
            if key == 'description':
                entry = scrolledtext.ScrolledText(details_frame, height=4, width=40)
                entry.grid(row=i, column=1, sticky='ew', pady=5, padx=5)
            elif key == 'password':
                entry = ttk.Entry(details_frame, show='*', width=40)
                entry.grid(row=i, column=1, sticky='ew', pady=5, padx=5)
            elif key == 'port':
                entry = ttk.Entry(details_frame, width=10)
                entry.grid(row=i, column=1, sticky='w', pady=5, padx=5)
                entry.insert(0, '22')
            else:
                entry = ttk.Entry(details_frame, width=40)
                entry.grid(row=i, column=1, sticky='ew', pady=5, padx=5)
            
            self.entries[key] = entry
        
        # Connection type
        type_frame = ttk.Frame(details_frame)
        type_frame.grid(row=len(form_fields), column=0, columnspan=2, pady=10)
        
        ttk.Label(type_frame, text="Connection Type:").pack(side=tk.LEFT)
        self.conn_type = tk.StringVar(value="SSH")
        ttk.Radiobutton(type_frame, text="SSH", variable=self.conn_type, value="SSH").pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(type_frame, text="Telnet", variable=self.conn_type, value="Telnet").pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(type_frame, text="Serial", variable=self.conn_type, value="Serial").pack(side=tk.LEFT, padx=10)
        
        # SSH key file
        key_frame = ttk.Frame(details_frame)
        key_frame.grid(row=len(form_fields)+1, column=0, columnspan=2, pady=5)
        
        ttk.Label(key_frame, text="Private Key:").pack(side=tk.LEFT)
        self.key_entry = ttk.Entry(key_frame, width=30)
        self.key_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(key_frame, text="Browse", command=self._browse_key).pack(side=tk.LEFT)
        
        # Buttons
        btn_frame = ttk.Frame(right_frame)
        btn_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(btn_frame, text="Save", command=self._save_connection).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self.top.destroy).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Connect", command=self._connect_now).pack(side=tk.RIGHT, padx=5)
        
        # Configure grid weights
        details_frame.grid_columnconfigure(1, weight=1)
        
        self.current_index = None
    
    def _load_connections_list(self):
        """Load connections into listbox."""
        self.conn_listbox.delete(0, tk.END)
        for conn in self.connections:
            name = conn.get('name', 'Unnamed')
            host = conn.get('host', '')
            self.conn_listbox.insert(tk.END, f"{name} ({host})")
    
    def _on_selection(self, event):
        """Handle connection selection."""
        selection = self.conn_listbox.curselection()
        if selection:
            self.current_index = selection[0]
            self._load_connection_details(self.connections[self.current_index])
    
    def _load_connection_details(self, conn):
        """Load connection details into form."""
        self.entries['name'].delete(0, tk.END)
        self.entries['name'].insert(0, conn.get('name', ''))
        
        self.entries['host'].delete(0, tk.END)
        self.entries['host'].insert(0, conn.get('host', ''))
        
        self.entries['port'].delete(0, tk.END)
        self.entries['port'].insert(0, str(conn.get('port', 22)))
        
        self.entries['username'].delete(0, tk.END)
        self.entries['username'].insert(0, conn.get('username', ''))
        
        self.entries['password'].delete(0, tk.END)
        self.entries['password'].insert(0, conn.get('password', ''))
        
        self.entries['description'].delete('1.0', tk.END)
        self.entries['description'].insert('1.0', conn.get('description', ''))
        
        self.conn_type.set(conn.get('type', 'SSH'))
        
        self.key_entry.delete(0, tk.END)
        self.key_entry.insert(0, conn.get('key_file', ''))
    
    def _new_connection(self):
        """Clear form for new connection."""
        self.current_index = None
        for key, entry in self.entries.items():
            if hasattr(entry, 'delete'):
                if key == 'description':
                    entry.delete('1.0', tk.END)
                else:
                    entry.delete(0, tk.END)
        
        if 'port' in self.entries:
            self.entries['port'].insert(0, '22')
        
        self.key_entry.delete(0, tk.END)
        self.conn_type.set('SSH')
    
    def _edit_connection(self):
        """Edit selected connection."""
        if self.current_index is None:
            messagebox.showwarning("Warning", "Please select a connection to edit, Ali!")
            return
        # Form already loaded via selection
    
    def _delete_connection(self):
        """Delete selected connection."""
        if self.current_index is None:
            messagebox.showwarning("Warning", "Please select a connection to delete, Ali!")
            return
        
        if messagebox.askyesno("Confirm", "Are you sure you want to delete this connection?"):
            del self.connections[self.current_index]
            self._load_connections_list()
            self._new_connection()
    
    def _save_connection(self):
        """Save connection details."""
        name = self.entries['name'].get().strip()
        host = self.entries['host'].get().strip()
        
        if not name or not host:
            messagebox.showerror("Error", "Name and Host are required, Ali!")
            return
        
        try:
            port = int(self.entries['port'].get())
        except ValueError:
            messagebox.showerror("Error", "Port must be a number, Ali!")
            return
        
        conn = {
            'name': name,
            'host': host,
            'port': port,
            'username': self.entries['username'].get().strip(),
            'password': self.entries['password'].get(),  # In production, encrypt this!
            'description': self.entries['description'].get('1.0', tk.END).strip(),
            'type': self.conn_type.get(),
            'key_file': self.key_entry.get().strip()
        }
        
        if self.current_index is not None:
            self.connections[self.current_index] = conn
        else:
            self.connections.append(conn)
        
        self._load_connections_list()
        messagebox.showinfo("Success", f"Connection '{name}' saved successfully, Ali!")
    
    def _connect_now(self):
        """Connect to selected/edited host."""
        host = self.entries['host'].get().strip()
        if not host:
            messagebox.showwarning("Warning", "Please enter a host, Ali!")
            return
        
        # Save first if needed
        self._save_connection()
        
        # Close dialog and trigger connection
        self.top.destroy()
        # Callback will handle the actual connection
    
    def _browse_key(self):
        """Browse for SSH private key file."""
        from tkinter import filedialog
        filename = filedialog.askopenfilename(
            title="Select Private Key",
            filetypes=[("Key files", "*.pem *.ppk *"), ("All files", "*.*")]
        )
        if filename:
            self.key_entry.delete(0, tk.END)
            self.key_entry.insert(0, filename)

# Import scrolledtext here to avoid circular dependency
from tkinter import scrolledtext

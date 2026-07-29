"""
Terminal widget module for NetMaster.
Provides terminal emulation with SSH/Telnet support.

For Ali - Network Engineer
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import queue

class TerminalWidget(ttk.Frame):
    """Terminal emulator widget with session management."""
    
    def __init__(self, parent, session_id=0):
        super().__init__(parent)
        self.session_id = session_id
        self.connected = False
        self.session = None  # Will hold SSH/Telnet session
        self.output_queue = queue.Queue()
        
        self._setup_ui()
        self._bind_events()
        self._start_output_processor()
    
    def _setup_ui(self):
        """Setup terminal UI components."""
        # Configure grid weights
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        
        # Terminal text area with dark theme
        self.terminal = scrolledtext.ScrolledText(
            self,
            bg='#1e1e1e',
            fg='#d4d4d4',
            insertbackground='#ffffff',
            font=('Consolas', 11),
            wrap=tk.NONE,
            undo=True,
            autoseparators=True
        )
        self.terminal.grid(row=0, column=0, sticky='nsew', padx=2, pady=2)
        
        # Configure text tags for colors
        self.terminal.tag_configure('prompt', foreground='#4EC9B0')
        self.terminal.tag_configure('output', foreground='#d4d4d4')
        self.terminal.tag_configure('error', foreground='#f48771')
        self.terminal.tag_configure('warning', foreground='#dcdcaa')
        self.terminal.tag_configure('success', foreground='#6a9955')
        self.terminal.tag_configure('info', foreground='#569cd6')
        
        # Command input line
        self.input_frame = ttk.Frame(self)
        self.input_frame.grid(row=1, column=0, sticky='ew', padx=2, pady=2)
        
        ttk.Label(self.input_frame, text="$", width=3).pack(side=tk.LEFT)
        self.command_entry = ttk.Entry(self.input_frame, font=('Consolas', 11))
        self.command_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Connection status indicator
        self.status_label = ttk.Label(
            self, 
            text="Disconnected", 
            style='Disconnected.TLabel'
        )
        self.status_label.grid(row=0, column=1, sticky='ne', padx=5, pady=5)
    
    def _bind_events(self):
        """Bind keyboard and mouse events."""
        self.command_entry.bind('<Return>', self._send_command)
        self.command_entry.bind('<Tab>', self._auto_complete)
        self.terminal.bind('<Key>', self._on_key_press)
        
        # Context menu
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Copy", command=self._copy)
        self.context_menu.add_command(label="Paste", command=self._paste)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Clear", command=self._clear_terminal)
        self.context_menu.add_command(label="Select All", command=self._select_all)
        
        self.terminal.bind('<Button-3>', self._show_context_menu)
    
    def _show_context_menu(self, event):
        """Show context menu on right-click."""
        self.context_menu.post(event.x_root, event.y_root)
    
    def _send_command(self, event=None):
        """Send command to terminal session."""
        command = self.command_entry.get().strip()
        if command and self.connected:
            self._write(f"$ {command}\n", 'prompt')
            self.command_entry.delete(0, tk.END)
            
            # Send command to session (will be implemented with paramiko)
            threading.Thread(target=self._execute_command, args=(command,), daemon=True).start()
        elif not self.connected:
            self._write("Not connected. Use Connect menu to establish session.\n", 'error')
            self.command_entry.delete(0, tk.END)
    
    def _execute_command(self, command):
        """Execute command in session (placeholder)."""
        # This will integrate with paramiko for SSH
        try:
            # Simulate command execution
            import time
            time.sleep(0.5)
            response = f"Command '{command}' executed successfully.\n"
            self.output_queue.put((response, 'output'))
        except Exception as e:
            self.output_queue.put((f"Error: {str(e)}\n", 'error'))
    
    def _start_output_processor(self):
        """Start background thread to process output queue."""
        def process():
            while True:
                try:
                    msg, tag = self.output_queue.get(timeout=0.1)
                    self._write(msg, tag)
                except queue.Empty:
                    continue
                except Exception as e:
                    print(f"Output processor error: {e}")
        
        threading.Thread(target=process, daemon=True).start()
    
    def _write(self, text, tag='output'):
        """Write text to terminal with specified tag."""
        self.terminal.insert(tk.END, text, tag)
        self.terminal.see(tk.END)
    
    def _on_key_press(self, event):
        """Handle key press events."""
        # Prevent default behavior for certain keys
        if event.keysym == 'Tab':
            return 'break'
    
    def _auto_complete(self, event):
        """Auto-complete command (placeholder)."""
        # Will implement command auto-completion
        return 'break'
    
    def _copy(self):
        """Copy selected text."""
        try:
            text = self.terminal.selection_get()
            self.clipboard_clear()
            self.clipboard_append(text)
        except tk.TclError:
            pass  # No selection
    
    def _paste(self):
        """Paste from clipboard."""
        try:
            text = self.clipboard_get()
            self.command_entry.insert(tk.END, text)
        except tk.TclError:
            pass  # Clipboard empty
    
    def _clear_terminal(self):
        """Clear terminal screen."""
        self.terminal.delete('1.0', tk.END)
        self._write("Terminal cleared.\n", 'info')
    
    def _select_all(self):
        """Select all text."""
        self.terminal.tag_add(tk.SEL, '1.0', tk.END)
        return 'break'
    
    def connect(self, host, port=22, username='', password='', key_file=None):
        """Establish SSH/Telnet connection."""
        # Will integrate with paramiko
        self._write(f"Connecting to {host}:{port}...\n", 'info')
        
        def do_connect():
            try:
                # Placeholder for actual connection
                import time
                time.sleep(1)
                self.connected = True
                self.output_queue.put((f"Connected to {host}\n", 'success'))
                self.status_label.config(text="Connected", style='Connected.TLabel')
            except Exception as e:
                self.output_queue.put((f"Connection failed: {str(e)}\n", 'error'))
        
        threading.Thread(target=do_connect, daemon=True).start()
    
    def disconnect(self):
        """Close current session."""
        if self.connected:
            self.connected = False
            self.session = None
            self._write("Session disconnected.\n", 'warning')
            self.status_label.config(text="Disconnected", style='Disconnected.TLabel')
    
    def is_connected(self):
        """Check if session is active."""
        return self.connected

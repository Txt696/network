"""
SSH Client module for NetMaster.
Provides SSH connectivity using Paramiko library.

For Ali - Network Engineer
"""

import paramiko
import threading
import queue
import time
from pathlib import Path


class SSHClient:
    """SSH client wrapper with Paramiko."""
    
    def __init__(self):
        self.client = None
        self.channel = None
        self.connected = False
        self.hostname = None
        self.port = 22
        self.username = None
        self.output_queue = queue.Queue()
        self._stop_receiving = False
    
    def connect(self, hostname, port=22, username='', password='', 
                key_file=None, timeout=10):
        """Establish SSH connection."""
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            self.hostname = hostname
            self.port = port
            self.username = username
            
            # Determine authentication method
            if key_file and Path(key_file).exists():
                # Use private key
                key = paramiko.RSAKey.from_private_key_file(key_file)
                self.client.connect(
                    hostname, 
                    port=port, 
                    username=username,
                    pkey=key,
                    timeout=timeout,
                    allow_agent=False,
                    look_for_keys=False
                )
            elif password:
                # Use password
                self.client.connect(
                    hostname, 
                    port=port, 
                    username=username,
                    password=password,
                    timeout=timeout
                )
            else:
                # Try default methods
                self.client.connect(
                    hostname, 
                    port=port, 
                    username=username,
                    timeout=timeout
                )
            
            # Open interactive channel
            self.channel = self.client.invoke_shell(term='xterm')
            self.channel.settimeout(0.5)  # Non-blocking read
            
            self.connected = True
            self._stop_receiving = False
            
            # Start receiver thread
            threading.Thread(target=self._receive_output, daemon=True).start()
            
            return True, "Connected successfully"
            
        except Exception as e:
            error_msg = str(e)
            self.connected = False
            return False, f"Connection failed: {error_msg}"
    
    def _receive_output(self):
        """Background thread to receive output from SSH session."""
        while self.connected and not self._stop_receiving:
            try:
                if self.channel.recv_ready():
                    data = self.channel.recv(4096).decode('utf-8', errors='replace')
                    if data:
                        self.output_queue.put(data)
                else:
                    time.sleep(0.1)
                    
                # Check if channel is closed
                if self.channel.exit_status_ready():
                    break
                    
            except Exception as e:
                if 'Socket is closed' in str(e) or 'EOF' in str(e):
                    break
                time.sleep(0.5)
        
        self.connected = False
        self.output_queue.put(None)  # Signal end of stream
    
    def send_command(self, command):
        """Send command to SSH session."""
        if self.connected and self.channel:
            try:
                # Add newline if not present
                if not command.endswith('\n'):
                    command += '\n'
                self.channel.send(command)
                return True
            except Exception as e:
                return False
        return False
    
    def resize_terminal(self, width, height):
        """Resize terminal window."""
        if self.connected and self.channel:
            try:
                self.channel.resize_pty(width=width, height=height)
            except Exception:
                pass
    
    def disconnect(self):
        """Close SSH connection."""
        self._stop_receiving = True
        self.connected = False
        
        try:
            if self.channel:
                self.channel.close()
            if self.client:
                self.client.close()
        except Exception:
            pass
        
        self.channel = None
        self.client = None
    
    def is_connected(self):
        """Check if connection is active."""
        return self.connected and self.channel and not self.channel.closed
    
    def get_output(self, timeout=0.1):
        """Get available output from queue."""
        try:
            return self.output_queue.get(timeout=timeout)
        except queue.Empty:
            return None


class TelnetClient:
    """Simple Telnet client (placeholder for future implementation)."""
    
    def __init__(self):
        self.connected = False
        # Will implement with telnetlib or custom socket
    
    def connect(self, hostname, port=23, username='', password='', timeout=10):
        """Establish Telnet connection."""
        # Placeholder
        return False, "Telnet not yet implemented, Ali!"
    
    def send_command(self, command):
        """Send command."""
        return False
    
    def disconnect(self):
        """Close connection."""
        self.connected = False

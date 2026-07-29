#!/usr/bin/env python3
"""
NetMaster - Professional Network Engineer Tool
Cross-platform terminal application for network engineers.

Author: AI Assistant
For: Ali
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.main_window import MainWindow
import tkinter as tk
from tkinter import ttk

def main():
    """Main entry point for NetMaster application."""
    print("Starting NetMaster for Ali...")
    
    # Create main application window
    root = tk.Tk()
    root.title("NetMaster - Network Engineer Tool")
    root.geometry("1200x800")
    
    # Set minimum size
    root.minsize(800, 600)
    
    # Initialize main window
    app = MainWindow(root)
    
    # Start main loop
    root.mainloop()

if __name__ == "__main__":
    main()

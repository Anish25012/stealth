"""Stealth desktop app: a local web server shown in a native window, with a tray icon.

Two processes share one executable. The agent owns the tray and the server, and the window
process only displays pages. macOS needs both pystray and pywebview on the main thread, so they
can't share a process.
"""

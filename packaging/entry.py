"""PyInstaller's entry script. Kept outside the package so the bundle runs it as __main__."""

from stealth_app.launcher import main

raise SystemExit(main())

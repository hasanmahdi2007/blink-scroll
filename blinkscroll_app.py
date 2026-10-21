"""Entry point for the packaged app (PyInstaller): same as ``python -m blinkscroll``."""

import sys

from blinkscroll.__main__ import main

if __name__ == "__main__":
    sys.exit(main())

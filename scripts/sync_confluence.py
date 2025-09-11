#!/usr/bin/env python3
"""
Shim entrypoint to run the current (Sep 11) modular sync script.
This preserves CI compatibility which invokes `python scripts/sync_confluence.py`.
"""
from sync_confluence_vSept11 import main


if __name__ == "__main__":
    main()


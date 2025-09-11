#!/usr/bin/env python3
"""
Shim entrypoint to run the vSept11.2 primary script.
This preserves CI compatibility which invokes `python scripts/sync_confluence.py`.
"""
from sync_confluence_vSept11_2 import main


if __name__ == "__main__":
    main()

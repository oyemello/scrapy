#!/usr/bin/env python3
"""
Shim entrypoint to run the vSept15 script (current primary).
This preserves CI compatibility which invokes `python scripts/sync_confluence.py`.
"""
from sync_confluence_vSept15 import main


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
bot.__main__ — python -m bot entry point.

Dispatches to bot.main.main() so the full trading bot is launched via:
  python -m bot

Exports: (none — side-effect module; calls main() when invoked as __main__)
"""
from bot.main import main

if __name__ == "__main__":
    main()

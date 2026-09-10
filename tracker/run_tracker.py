#!/usr/bin/env python3
"""Start the TaikoEX tracker.  See ``python run_tracker.py --help``."""
import sys

from taiko_tracker.tracker import main

if __name__ == "__main__":
    sys.exit(main())

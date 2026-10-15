"""Repair garbled emoji text - the logic is auto_uploader/utils/unmojibake.py.

    python tools/unmojibake.py --config      fix auto_uploader/config.json in place
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "auto_uploader"))

from utils.unmojibake import fix, fix_tree, main  # noqa: E402,F401

if __name__ == "__main__":
    sys.exit(main())

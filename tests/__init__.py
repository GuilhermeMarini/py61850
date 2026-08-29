"""Offline test suite (standard-library ``unittest``, no test dependencies).

Adds ``src/`` to the path so the suite runs from a bare checkout, without an
editable install.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

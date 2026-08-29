"""``python -m py61850.cli`` -- same as the ``py61850`` command."""

import sys

from .main import main

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)

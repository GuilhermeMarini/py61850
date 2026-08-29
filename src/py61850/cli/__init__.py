"""Command-line drivers.

Installed as ``py61850`` (subcommands), plus ``mms-scan`` / ``mms-files`` for
the two MMS drivers directly.

This is the only layer allowed to ``print`` or ``sys.exit``: the library under
``core``/``osi``/``mms`` returns values and raises exceptions.
"""

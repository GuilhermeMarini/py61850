# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The package's contract: what ``py61850`` exports, and what it must not
import at package-import time."""

import os
import subprocess
import sys
import unittest

import py61850

# The subprocess checks below must find the same py61850 this process did,
# whether the suite runs from an editable install or a bare checkout.
_ENV = dict(os.environ)
_ENV["PYTHONPATH"] = os.pathsep.join(
    [os.path.dirname(os.path.dirname(py61850.__file__))] + sys.path[:1])


def _run(code):
    return subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, env=_ENV)


class TestPublicApi(unittest.TestCase):
    def test_all_names_exist(self):
        for name in py61850.__all__:
            self.assertTrue(hasattr(py61850, name), name)

    def test_expected_surface(self):
        self.assertEqual(sorted(py61850.__all__), sorted([
            "MmsClient", "FileTransfer", "DirEntry", "LogicalNode", "folder_of",
            "FUNCTIONAL_CONSTRAINTS", "fc_is_control", "fc_read_rank",
            "Iec61850Error", "TransportError", "MmsError",
            "LinkError", "GooseError", "SvError", "SclError",
            "decode_read_response", "decode_data_definition", "decode_service_error",
            "__version__",
        ]))

    def test_error_tree(self):
        for name in ("TransportError", "MmsError", "LinkError",
                     "GooseError", "SvError", "SclError"):
            self.assertTrue(issubclass(getattr(py61850, name), py61850.Iec61850Error))

    def test_file_transfer_is_a_client(self):
        self.assertTrue(issubclass(py61850.FileTransfer, py61850.MmsClient))


class TestLazyBoundaries(unittest.TestCase):
    """The L2 packages must stay out of the import graph: the MMS client has to
    keep installing and running unprivileged, on any OS."""

    def test_importing_py61850_does_not_pull_in_l2_or_scl(self):
        code = ("import sys; import py61850; "
                "print([m for m in sys.modules "
                "if m.startswith(('py61850.link', 'py61850.goose', "
                "'py61850.sv', 'py61850.scl'))])")
        out = _run(code)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[]")

    def test_importing_py61850_does_not_open_a_socket_module_connection(self):
        """The library must not do I/O on import."""
        code = ("import socket; socket.socket = None; "
                "import py61850; print(py61850.__version__)")
        out = _run(code)
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()

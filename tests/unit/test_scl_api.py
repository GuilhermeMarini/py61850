# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""What `py61850.scl` exports, and the import boundary it must not cross."""

import os
import subprocess
import sys
import unittest

import py61850
import py61850.scl as scl

_ENV = dict(os.environ)
_ENV["PYTHONPATH"] = os.pathsep.join(
    [os.path.dirname(os.path.dirname(py61850.__file__))] + sys.path[:1])


class TestSurface(unittest.TestCase):
    def test_all_names_exist(self):
        for name in scl.__all__:
            self.assertTrue(hasattr(scl, name), name)

    def test_expected_surface(self):
        self.assertEqual(sorted(scl.__all__), sorted([
            "SclDocument", "Header",
            "DtdNotAllowed", "reject_dtd_in_file", "reject_dtd_in_bytes",
            "TemplatePool", "LNodeTypeSpec", "DoTypeSpec", "AttributeSpec",
            "Communication", "SubNetwork", "ConnectedAP", "Address",
            "ControlBlockAddress",
            "IedHeader", "Ied", "AccessPoint", "Server", "LDevice",
            "LogicalNode", "DataObject", "DataAttribute",
            "DataSet", "FCDA", "ControlBlock", "SettingControl", "ExtRef",
            "strip_ns", "iter_local", "children_local", "privates_of",
            "Insert", "Remove", "SetAttributes", "SetTextContent",
            "EditRejected",
        ]))

    def test_the_dtd_refusal_is_public(self):
        # It was private in the library this moved from, and a consumer in
        # another repository was importing it through the underscore anyway.
        self.assertIs(scl.DtdNotAllowed, py61850.scl._xmlsafe.DtdNotAllowed)


class TestImportBoundary(unittest.TestCase):
    def test_importing_py61850_still_does_not_pull_in_scl(self):
        # The MMS client has to keep installing and running unprivileged on
        # any OS; the SCL package is reached explicitly or not at all.
        code = ("import sys; import py61850; "
                "print([m for m in sys.modules if m.startswith('py61850.scl')])")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=_ENV)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[]")

    def test_scl_does_no_io_on_import(self):
        code = ("import socket; socket.socket = None; "
                "import py61850.scl as s; print(len(s.__all__))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=_ENV)
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()

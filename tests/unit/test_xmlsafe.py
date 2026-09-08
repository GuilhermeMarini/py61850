# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Refusing a DTD before anything can expand it."""

import os
import tempfile
import unittest

from py61850.errors import Iec61850Error
from py61850.scl import _xmlsafe

_BILLION_LAUGHS = (
    b'<?xml version="1.0"?>\n'
    b'<!DOCTYPE SCL [\n'
    b'  <!ENTITY a "AAAAAAAAAA">\n'
    b'  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
    b']>\n'
    b'<SCL><Header id="x"/></SCL>\n'
)

_CLEAN = b'<?xml version="1.0"?>\n<SCL><Header id="x"/></SCL>\n'


class TestRejectBytes(unittest.TestCase):
    def test_a_doctype_is_refused(self):
        with self.assertRaises(_xmlsafe.DtdNotAllowed):
            _xmlsafe.reject_dtd_in_bytes(_BILLION_LAUGHS)

    def test_a_clean_document_passes(self):
        _xmlsafe.reject_dtd_in_bytes(_CLEAN)     # must not raise

    def test_a_malformed_prolog_is_not_this_module_s_problem(self):
        # Swallowed on purpose: the real parse is about to hit the same bytes
        # with the same grammar and will report it properly. This is not a way
        # past the check -- a prolog expat cannot read, ET.parse cannot read.
        _xmlsafe.reject_dtd_in_bytes(b'<?xml version="1.0"?><<<')

    def test_a_doctype_behind_a_large_comment_is_still_found(self):
        # The shape that defeats a "look at the first N bytes" check.
        padded = (b'<?xml version="1.0"?>\n<!--' + b'x' * 500_000 + b'-->\n'
                  + _BILLION_LAUGHS.split(b'\n', 1)[1])
        with self.assertRaises(_xmlsafe.DtdNotAllowed):
            _xmlsafe.reject_dtd_in_bytes(padded)


class TestRejectFile(unittest.TestCase):
    def _write(self, data):
        fd, path = tempfile.mkstemp(suffix=".scd")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        self.addCleanup(os.unlink, path)
        return path

    def test_a_doctype_is_refused(self):
        with self.assertRaises(_xmlsafe.DtdNotAllowed):
            _xmlsafe.reject_dtd_in_file(self._write(_BILLION_LAUGHS))

    def test_a_clean_file_passes(self):
        _xmlsafe.reject_dtd_in_file(self._write(_CLEAN))


class TestErrorTree(unittest.TestCase):
    def test_it_is_both_an_scl_error_and_a_value_error(self):
        # SclError so `except Iec61850Error` catches it; ValueError because
        # that is what it has always been, and callers catch it that way.
        self.assertTrue(issubclass(_xmlsafe.DtdNotAllowed, Iec61850Error))
        self.assertTrue(issubclass(_xmlsafe.DtdNotAllowed, ValueError))

    def test_the_message_names_the_doctype(self):
        with self.assertRaises(_xmlsafe.DtdNotAllowed) as caught:
            _xmlsafe.reject_dtd_in_bytes(_BILLION_LAUGHS)
        self.assertIn("SCL", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

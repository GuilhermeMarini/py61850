# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""TypeDescription decoding (GetVariableAccessAttributes / ACSI GetDataDefinition)."""

import unittest

from py61850.core import ber
from py61850.mms import types
from py61850.mms.service_error import decode_service_error
from py61850.mms.pdu import CONFIRMED_ERROR


def _component(name, type_tlv):
    """SEQUENCE { componentName [0] VisibleString, componentType [1] }."""
    return ber.tlv(0x30, ber.tlv(0x80, name.encode()) + ber.tlv(0xA1, type_tlv))


class TestTypeDescription(unittest.TestCase):
    def test_scalar_names(self):
        self.assertEqual(types.decode_type_description(0x83, b""), "boolean")
        self.assertEqual(types.decode_type_description(0x8A, b""), "visible-string")
        self.assertEqual(types.decode_type_description(0x91, b""), "utc-time")

    def test_sized_types_carry_their_width(self):
        self.assertEqual(types.decode_type_description(0x85, b"\x20"), {"integer": 32})
        self.assertEqual(types.decode_type_description(0x86, b"\x20"), {"unsigned": 32})
        self.assertEqual(types.decode_type_description(0x84, b"\x0d"), {"bit-string": 13})

    def test_bit_string_width_is_signed(self):
        """bit-string carries a signed Integer32; negative means a fixed width.
        An SEL-411L reports Quality as 0xf3 -- that is -13 bits, not 243."""
        self.assertEqual(types.decode_type_description(0x84, b"\xf3"),
                         {"bit-string": -13})

    def test_integer_width_stays_unsigned(self):
        """integer/unsigned carry an Unsigned8, so 0x80 is 128 and not -128."""
        self.assertEqual(types.decode_type_description(0x85, b"\x80"), {"integer": 128})

    def test_unknown_tag(self):
        self.assertEqual(types.decode_type_description(0x99, b""), "tag-0x99")

    def test_structure(self):
        comps = _component("stVal", ber.tlv(0x83, b""))
        comps += _component("q", ber.tlv(0x84, b"\x0d"))
        comps += _component("t", ber.tlv(0x91, b""))
        struct = ber.tlv(0xA2, ber.tlv(0xA1, comps))
        self.assertEqual(
            types.decode_type_description(0xA2, struct[2:]),
            {"structure": [
                {"name": "stVal", "type": "boolean"},
                {"name": "q", "type": {"bit-string": 13}},
                {"name": "t", "type": "utc-time"},
            ]})

    def test_data_definition_response(self):
        struct = ber.tlv(0xA2, ber.tlv(0xA1, _component("stVal", ber.tlv(0x83, b""))))
        resp = ber.tlv(0xA6, ber.tlv(0x80, b"\xff") + ber.tlv(0xA2, struct))
        self.assertEqual(
            types.decode_data_definition(resp),
            {"mmsDeletable": True,
             "type": {"structure": [{"name": "stVal", "type": "boolean"}]}})

    def test_quality_data_definition_from_hardware(self):
        """Raw bytes recorded from an SEL-411L: LLN0$ST$Beh$q."""
        raw = bytes.fromhex("a608800100a2038401f3")
        self.assertEqual(types.decode_data_definition(raw),
                         {"mmsDeletable": False, "type": {"bit-string": -13}})

    def test_data_definition_scalar(self):
        resp = ber.tlv(0xA6, ber.tlv(0x80, b"\x00") + ber.tlv(0xA2, ber.tlv(0x8A, b"")))
        self.assertEqual(types.decode_data_definition(resp),
                         {"mmsDeletable": False, "type": "visible-string"})


class TestServiceError(unittest.TestCase):
    def _error(self, class_tag, code):
        return ber.tlv(CONFIRMED_ERROR,
                       ber.tlv(0x80, b"\x2a")
                       + ber.tlv(0xA2, ber.tlv(0xA0, ber.tlv(class_tag, bytes([code])))))

    def test_file_errors_get_names(self):
        self.assertEqual(decode_service_error(self._error(0x8B, 6)),
                         "file/file-non-existent (invoke 42)")

    def test_other_classes_report_numeric_code(self):
        self.assertEqual(decode_service_error(self._error(0x87, 3)),
                         "access/3 (invoke 42)")

    def test_unknown_class(self):
        self.assertIn("class-0x8f", decode_service_error(self._error(0x8F, 1)))

    def test_unparsable_falls_back_to_hex(self):
        self.assertIn("service error", decode_service_error(b"\xa2\x02\x80"))


if __name__ == "__main__":
    unittest.main()

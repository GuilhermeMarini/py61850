# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""BER encode/decode edge cases, and agreement between the copying and
zero-copy decode paths."""

import unittest

from py61850.core import ber


class TestLengths(unittest.TestCase):
    def test_short_form(self):
        self.assertEqual(ber.enc_len(0), b"\x00")
        self.assertEqual(ber.enc_len(1), b"\x01")
        self.assertEqual(ber.enc_len(127), b"\x7f")

    def test_long_form(self):
        self.assertEqual(ber.enc_len(128), b"\x81\x80")
        self.assertEqual(ber.enc_len(255), b"\x81\xff")
        self.assertEqual(ber.enc_len(256), b"\x82\x01\x00")
        self.assertEqual(ber.enc_len(65535), b"\x82\xff\xff")

    def test_read_len_round_trip(self):
        for n in (0, 1, 127, 128, 255, 256, 4096, 65535):
            length, i = ber.read_len(ber.enc_len(n), 0)
            self.assertEqual(length, n)
            self.assertEqual(i, len(ber.enc_len(n)))


class TestTags(unittest.TestCase):
    def test_single_byte(self):
        self.assertEqual(ber.enc_tag(0x30), b"\x30")
        self.assertEqual(ber.enc_tag(0xA0), b"\xa0")

    def test_multi_byte(self):
        # fileOpen [72] is written as the pre-composed 0xBF48
        self.assertEqual(ber.enc_tag(0xBF48), b"\xbf\x48")
        self.assertEqual(ber.enc_tag(0x9F49), b"\x9f\x49")

    def test_read_tag_folds_high_tag_number_form(self):
        self.assertEqual(ber.read_tag(b"\xbf\x48\x00", 0), (0xBF48, 2))
        self.assertEqual(ber.read_tag(b"\x9f\x49\x00", 0), (0x9F49, 2))
        self.assertEqual(ber.read_tag(b"\x30\x00", 0), (0x30, 1))


class TestIntegers(unittest.TestCase):
    def test_enc_uint_pads_for_sign(self):
        self.assertEqual(ber.enc_uint(0), b"\x00")
        self.assertEqual(ber.enc_uint(127), b"\x7f")
        self.assertEqual(ber.enc_uint(128), b"\x00\x80")   # would read as -128 otherwise
        self.assertEqual(ber.enc_uint(255), b"\x00\xff")
        self.assertEqual(ber.enc_uint(256), b"\x01\x00")

    def test_enc_int_two_complement(self):
        self.assertEqual(ber.enc_int(0), b"\x00")
        self.assertEqual(ber.enc_int(-1), b"\xff")
        self.assertEqual(ber.enc_int(127), b"\x7f")
        self.assertEqual(ber.enc_int(128), b"\x00\x80")
        self.assertEqual(ber.enc_int(-128), b"\x80")
        self.assertEqual(ber.enc_int(-129), b"\xff\x7f")
        self.assertEqual(ber.enc_int(-256), b"\xff\x00")
        self.assertEqual(ber.enc_int(-32768), b"\x80\x00")

    def test_enc_int_round_trips_signed(self):
        for n in (0, 1, -1, 127, 128, -128, -129, -256, 32767, -32768, 10 ** 9, -10 ** 9):
            self.assertEqual(
                int.from_bytes(ber.enc_int(n), "big", signed=True), n)

    def test_enc_int_is_minimal_width(self):
        """A non-minimal encoding still decodes, so only a width check catches it."""
        for n, width in ((0, 1), (127, 1), (-128, 1), (128, 2), (-129, 2),
                         (32767, 2), (-32768, 2), (8388607, 3), (-8388608, 3)):
            self.assertEqual(len(ber.enc_int(n)), width, f"enc_int({n})")


class TestTlv(unittest.TestCase):
    def test_tlv_layout(self):
        self.assertEqual(ber.tlv(0x02, b"\x05"), b"\x02\x01\x05")
        self.assertEqual(ber.tlv(0xBF48, b"ab"), b"\xbf\x48\x02ab")

    def test_long_value(self):
        payload = b"\x00" * 200
        encoded = ber.tlv(0x04, payload)
        self.assertEqual(encoded[:3], b"\x04\x81\xc8")
        tag, value, nxt = ber.read_tlv(encoded, 0)
        self.assertEqual((tag, value), (0x04, payload))
        self.assertEqual(nxt, len(encoded))

    def test_iter_tlv_walks_concatenated(self):
        buf = ber.tlv(0x02, b"\x01") + ber.tlv(0x03, b"\x02\x03") + ber.tlv(0xBF48, b"")
        self.assertEqual(list(ber.iter_tlv(buf)),
                         [(0x02, b"\x01"), (0x03, b"\x02\x03"), (0xBF48, b"")])

    def test_empty_buffer(self):
        self.assertEqual(list(ber.iter_tlv(b"")), [])


class TestZeroCopyPath(unittest.TestCase):
    """iter_tlv_view is the SV hot path; it must agree with iter_tlv exactly."""

    BUF = (ber.tlv(0x02, b"\x01")
           + ber.tlv(0xA2, ber.tlv(0x85, b"\x7f") + ber.tlv(0x8A, b"hello"))
           + ber.tlv(0x04, b"\xaa" * 300))

    def test_views_match_copies(self):
        copies = list(ber.iter_tlv(self.BUF))
        views = [(t, bytes(v)) for t, v in ber.iter_tlv_view(self.BUF)]
        self.assertEqual(views, copies)

    def test_views_do_not_copy(self):
        mv = memoryview(self.BUF)
        for _, v in ber.iter_tlv_view(mv):
            self.assertIsInstance(v, memoryview)
            self.assertEqual(v.obj, mv.obj)          # aliases the same buffer

    def test_read_tlv_at_offsets(self):
        tag, start, end, nxt = ber.read_tlv_at(self.BUF, 0)
        self.assertEqual(tag, 0x02)
        self.assertEqual(self.BUF[start:end], b"\x01")
        self.assertEqual(nxt, end)

    def test_read_tlv_accepts_memoryview(self):
        tag, value, _ = ber.read_tlv(memoryview(self.BUF), 0)
        self.assertEqual((tag, value), (0x02, b"\x01"))
        self.assertIsInstance(value, bytes)


if __name__ == "__main__":
    unittest.main()

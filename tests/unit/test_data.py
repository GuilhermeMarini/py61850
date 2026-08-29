"""MMS Data values: every encoder round-trips through decode_data, and the
wire bytes are what the spec says -- a round-trip alone would pass even if the
pair were symmetrically wrong."""

import unittest

from py61850.core import ber
from py61850.core import data as d
from py61850.core.quality import Quality


def roundtrip(encoded):
    tag, value, nxt = ber.read_tlv(encoded, 0)
    assert nxt == len(encoded), "encoder emitted trailing bytes"
    return d.decode_data(tag, value)


class TestWireBytes(unittest.TestCase):
    def test_boolean(self):
        self.assertEqual(d.encode_boolean(True), b"\x83\x01\xff")
        self.assertEqual(d.encode_boolean(False), b"\x83\x01\x00")

    def test_integer(self):
        self.assertEqual(d.encode_integer(-5), b"\x85\x01\xfb")
        self.assertEqual(d.encode_integer(1), b"\x85\x01\x01")

    def test_unsigned(self):
        self.assertEqual(d.encode_unsigned(200), b"\x86\x02\x00\xc8")
        with self.assertRaises(ValueError):
            d.encode_unsigned(-1)

    def test_float_carries_exponent_width(self):
        # FloatingPoint = one exponent-width octet, then IEEE 754
        self.assertEqual(d.encode_float(1.5), b"\x87\x05\x08\x3f\xc0\x00\x00")
        self.assertEqual(d.encode_float(1.5, double=True),
                         b"\x87\x09\x0b\x3f\xf8\x00\x00\x00\x00\x00\x00")

    def test_visible_string(self):
        self.assertEqual(d.encode_visible_string("hi"), b"\x8a\x02hi")

    def test_octet_string(self):
        self.assertEqual(d.encode_octet_string(b"\x01\x02"), b"\x89\x02\x01\x02")

    def test_bitstring_counts_unused_bits(self):
        # 7 bits -> 1 unused, padded right: "0011010" + "0" = 0x34
        self.assertEqual(d.encode_bitstring("0011010"), b"\x84\x02\x01\x34")
        self.assertEqual(d.encode_bitstring("11111111"), b"\x84\x02\x00\xff")
        self.assertEqual(d.encode_bitstring(""), b"\x84\x01\x00")

    def test_structure_is_constructed(self):
        self.assertEqual(d.encode_structure([True, 1]),
                         b"\xa2\x06\x83\x01\xff\x85\x01\x01")
        self.assertEqual(d.encode_array([True]), b"\xa1\x03\x83\x01\xff")


class TestRoundTrip(unittest.TestCase):
    def test_scalars(self):
        self.assertIs(roundtrip(d.encode_boolean(True)), True)
        self.assertIs(roundtrip(d.encode_boolean(False)), False)
        for n in (0, 1, -1, 127, -128, 32767, -32768, 10 ** 9):
            self.assertEqual(roundtrip(d.encode_integer(n)), n)
        for n in (0, 1, 200, 65535, 2 ** 31):
            self.assertEqual(roundtrip(d.encode_unsigned(n)), n)
        self.assertEqual(roundtrip(d.encode_visible_string("LD0/LLN0")), "LD0/LLN0")

    def test_float(self):
        self.assertEqual(roundtrip(d.encode_float(1.5)), 1.5)
        self.assertAlmostEqual(roundtrip(d.encode_float(3.14159)), 3.14159, places=5)
        self.assertEqual(roundtrip(d.encode_float(2.718281828, double=True)),
                         2.718281828)

    def test_bitstring(self):
        for bits in ("", "1", "0011010", "11111111", "0" * 13, "1010101010101"):
            self.assertEqual(roundtrip(d.encode_bitstring(bits)), bits)

    def test_utc_time(self):
        got = roundtrip(d.encode_utc(1700000000.5, quality=0x20))
        self.assertEqual(got["quality"], 0x20)
        self.assertAlmostEqual(got["utc"], 1700000000.5, places=6)

    def test_nested_structure(self):
        value = [True, [1, "a"], 2.5]
        encoded = d.encode_structure(value)
        self.assertEqual(roundtrip(encoded), [True, [1, "a"], 2.5])

    def test_octet_string_decodes_to_hex(self):
        """Asymmetric on purpose: decode renders octet-strings as hex text."""
        self.assertEqual(roundtrip(d.encode_octet_string(b"\x01\xff")), "01ff")


class TestInference(unittest.TestCase):
    def test_bool_is_not_int(self):
        """bool subclasses int -- the boolean branch must be checked first."""
        self.assertEqual(d.encode_data(True), b"\x83\x01\xff")
        self.assertEqual(d.encode_data(1), b"\x85\x01\x01")

    def test_inferred_types(self):
        self.assertEqual(roundtrip(d.encode_data(-7)), -7)
        self.assertEqual(roundtrip(d.encode_data("x")), "x")
        self.assertEqual(roundtrip(d.encode_data(1.5)), 1.5)
        self.assertEqual(roundtrip(d.encode_data([1, 2])), [1, 2])

    def test_list_defaults_to_structure(self):
        self.assertEqual(d.encode_data([True])[0], d.STRUCTURE)
        self.assertEqual(d.encode_data([True], as_array=True)[0], d.ARRAY)

    def test_unencodable(self):
        with self.assertRaises(TypeError):
            d.encode_data(None)
        with self.assertRaises(TypeError):
            d.encode_data(object())


class TestQuality(unittest.TestCase):
    def test_good(self):
        q = Quality("0000000000000")
        self.assertEqual(q.validity, "good")
        self.assertEqual(q.source, "process")
        self.assertTrue(q.is_good())
        self.assertFalse(q.test)

    def test_flags(self):
        q = Quality("0100000010001")
        self.assertEqual(q.validity, "invalid")
        self.assertTrue(q.inconsistent)
        self.assertTrue(q.operator_blocked)
        self.assertFalse(q.is_good())

    def test_substituted_source_and_test(self):
        q = Quality("0000000000110")
        self.assertEqual(q.source, "substituted")
        self.assertTrue(q.test)

    def test_old_data_is_not_good(self):
        q = Quality("0000000100000")          # bit 7 = oldData
        self.assertEqual(q.validity, "good")
        self.assertTrue(q.old_data)
        self.assertFalse(q.failure)
        self.assertFalse(q.is_good())

    def test_accepts_short_bitstring(self):
        """Relays may send fewer than 13 bits; missing trailing bits read false."""
        self.assertEqual(Quality("00").validity, "good")

    def test_decoded_bitstring_feeds_quality(self):
        bits = roundtrip(d.encode_bitstring("0100000000000"))
        self.assertEqual(Quality(bits).validity, "invalid")


if __name__ == "__main__":
    unittest.main()

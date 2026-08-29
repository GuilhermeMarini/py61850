"""MMS PDU builders and decoders -- the whole point of keeping mms.pdu free of
sockets is that all of this runs with no relay in sight.

Expected byte strings are written out from the ASN.1 structure rather than
captured from the builders, so a builder that changes shape fails here.
"""

import unittest

from py61850.core import ber
from py61850.core import data as d
from py61850.errors import MmsError
from py61850.mms import pdu


class TestEnvelope(unittest.TestCase):
    def test_confirmed_request(self):
        self.assertEqual(pdu.confirmed_request(5, b"\xa4\x00"),
                         b"\xa0\x05" b"\x02\x01\x05" b"\xa4\x00")

    def test_confirmed_response_mirrors_request(self):
        self.assertEqual(pdu.confirmed_response(5, b"\xa4\x00"),
                         b"\xa1\x05" b"\x02\x01\x05" b"\xa4\x00")

    def test_object_name_is_domain_specific(self):
        self.assertEqual(pdu.object_name("LD0", "X"),
                         b"\xa1\x08" b"\x1a\x03LD0" b"\x1a\x01X")

    def test_service_from_response_strips_invoke_id(self):
        service = ber.tlv(pdu.SVC_READ, b"\x00")
        self.assertEqual(
            pdu.service_from_response(pdu.confirmed_response(9, service)), service)

    def test_service_error_raises(self):
        # confirmed-ErrorPDU { invokeID, serviceError { errorClass file/file-busy } }
        err = ber.tlv(pdu.CONFIRMED_ERROR,
                      ber.tlv(0x80, b"\x03")
                      + ber.tlv(0xA2, ber.tlv(0xA0, ber.tlv(0x8B, b"\x01"))))
        with self.assertRaises(MmsError) as cm:
            pdu.service_from_response(err)
        self.assertIn("file/file-busy", str(cm.exception))
        self.assertIn("invoke 3", str(cm.exception))

    def test_reject_raises(self):
        with self.assertRaises(MmsError):
            pdu.service_from_response(ber.tlv(pdu.REJECT, b"\x00"))

    def test_unexpected_tag_raises(self):
        with self.assertRaises(MmsError):
            pdu.service_from_response(b"\xaf\x00")


class TestGetNameList(unittest.TestCase):
    def test_vmd_scope(self):
        self.assertEqual(
            pdu.build_get_name_list(pdu.CLASS_DOMAIN, "vmd"),
            b"\xa1\x09" b"\xa0\x03\x80\x01\x09" b"\xa1\x02\x80\x00")

    def test_domain_scope_with_continuation(self):
        self.assertEqual(
            pdu.build_get_name_list(pdu.CLASS_NAMED_VARIABLE, "domain", "LD0", "A"),
            b"\xa1\x0f"
            b"\xa0\x03\x80\x01\x00"          # objectClass namedVariable(0)
            b"\xa1\x05\x81\x03LD0"           # scope domainSpecific "LD0"
            b"\x82\x01A")                    # continueAfter "A"

    def test_bad_scope(self):
        with self.assertRaises(ValueError):
            pdu.build_get_name_list(pdu.CLASS_DOMAIN, "nonsense")

    def test_decode(self):
        resp = ber.tlv(pdu.SVC_GET_NAME_LIST,
                       ber.tlv(0xA0, ber.tlv(0x1A, b"LD0") + ber.tlv(0x1A, b"LD1"))
                       + ber.tlv(0x81, b"\xff"))
        self.assertEqual(pdu.decode_name_list(resp), (["LD0", "LD1"], True))

    def test_decode_without_more_follows(self):
        resp = ber.tlv(pdu.SVC_GET_NAME_LIST, ber.tlv(0xA0, ber.tlv(0x1A, b"LD0")))
        self.assertEqual(pdu.decode_name_list(resp), (["LD0"], False))


class TestRead(unittest.TestCase):
    def test_build(self):
        # read [4] { varAccessSpec [1] { listOfVariable [0] SEQ OF { name [0] } } }
        self.assertEqual(
            pdu.build_read("LD0", "X"),
            b"\xa4\x12\xa1\x10\xa0\x0e\x30\x0c\xa0\x0a"
            b"\xa1\x08\x1a\x03LD0\x1a\x01X")

    def test_decode_value(self):
        resp = ber.tlv(pdu.SVC_READ, ber.tlv(0xA1, d.encode_boolean(True)))
        self.assertEqual(pdu.decode_read_response(resp), [True])

    def test_decode_multiple(self):
        resp = ber.tlv(pdu.SVC_READ, ber.tlv(
            0xA1, d.encode_integer(3) + d.encode_visible_string("ok")))
        self.assertEqual(pdu.decode_read_response(resp), [3, "ok"])

    def test_decode_access_failure(self):
        resp = ber.tlv(pdu.SVC_READ, ber.tlv(0xA1, ber.tlv(0x80, b"\x0a")))
        self.assertEqual(pdu.decode_read_response(resp),
                         [{"error": "object-non-existent"}])

    def test_decode_unknown_error_code(self):
        resp = ber.tlv(pdu.SVC_READ, ber.tlv(0xA1, ber.tlv(0x80, b"\x63")))
        self.assertEqual(pdu.decode_read_response(resp), [{"error": "error-99"}])


class TestGetVariableAccessAttributes(unittest.TestCase):
    def test_build(self):
        self.assertEqual(
            pdu.build_get_var_access_attributes("LD0", "X"),
            b"\xa6\x0c\xa0\x0a\xa1\x08\x1a\x03LD0\x1a\x01X")


class TestFileServices(unittest.TestCase):
    def test_build_file_read_uses_primitive_tag(self):
        """FileRead carries an Integer32, so its tag is 0x9F49, not 0xBF49."""
        self.assertEqual(pdu.build_file_read(3), b"\x9f\x49\x01\x03")
        self.assertEqual(pdu.build_file_close(3), b"\x9f\x4a\x01\x03")

    def test_build_file_open_uses_constructed_tag(self):
        self.assertEqual(pdu.build_file_open("/A", 0),
                         b"\xbf\x48\x09" b"\xa0\x04\x19\x02/A" b"\x81\x01\x00")

    def test_build_file_directory(self):
        self.assertEqual(pdu.build_file_directory(), b"\xbf\x4d\x00")
        self.assertEqual(pdu.build_file_directory("/E/"),
                         b"\xbf\x4d\x07\xa0\x05\x19\x03/E/")

    @staticmethod
    def _entry(name, size, modified):
        attrs = ber.tlv(0x80, ber.enc_uint(size)) + ber.tlv(0x81, modified.encode())
        return ber.tlv(0x30, ber.tlv(0xA0, ber.tlv(0x19, name.encode()))
                       + ber.tlv(0xA1, attrs))

    def test_decode_directory(self):
        body = ber.tlv(0xA0, self._entry("/CFG.TXT", 1234, "20240101120000Z"))
        body += ber.tlv(0x81, b"\x00")
        entries, more = pdu.decode_file_directory(ber.tlv(pdu.SVC_FILE_DIRECTORY, body))
        self.assertFalse(more)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "/CFG.TXT")
        self.assertEqual(entries[0].size, 1234)
        self.assertEqual(entries[0].last_modified, "20240101120000Z")

    def test_decode_directory_with_vendor_wrapper_sequence(self):
        """Some relays wrap the entry list in one extra SEQUENCE."""
        inner = self._entry("/A.TXT", 1, "20240101120000Z")
        inner += self._entry("/B.TXT", 2, "20240101120001Z")
        wrapped = ber.tlv(pdu.SVC_FILE_DIRECTORY,
                          ber.tlv(0xA0, ber.tlv(0x30, inner)) + ber.tlv(0x81, b"\xff"))
        entries, more = pdu.decode_file_directory(wrapped)
        self.assertTrue(more)
        self.assertEqual([e.name for e in entries], ["/A.TXT", "/B.TXT"])

    def test_decode_file_open(self):
        body = ber.tlv(0x80, b"\x07") + ber.tlv(0xA1, ber.tlv(0x80, ber.enc_uint(500)))
        self.assertEqual(pdu.decode_file_open(ber.tlv(pdu.SVC_FILE_OPEN, body)),
                         (7, 500))

    def test_decode_file_read_defaults_more_follows_true(self):
        resp = ber.tlv(pdu.SVC_FILE_READ, ber.tlv(0x80, b"abc"))
        self.assertEqual(pdu.decode_file_read(resp), (b"abc", True))

    def test_decode_file_read_final_chunk(self):
        resp = ber.tlv(pdu.SVC_FILE_READ,
                       ber.tlv(0x80, b"abc") + ber.tlv(0x81, b"\x00"))
        self.assertEqual(pdu.decode_file_read(resp), (b"abc", False))

    def test_dir_entry_repr_handles_missing_timestamp(self):
        self.assertIn("/X", repr(pdu.DirEntry("/X", 0, None)))


class TestInitiate(unittest.TestCase):
    def test_tag_and_parses(self):
        init = pdu.build_initiate()
        self.assertEqual(init[0], pdu.INITIATE_REQUEST)
        tag, body, nxt = ber.read_tlv(init, 0)
        self.assertEqual(nxt, len(init))
        fields = dict(ber.iter_tlv(body))
        self.assertEqual(fields[0x81], b"\x05")          # maxServOutstandingCalling
        self.assertIn(0xA4, fields)                      # initRequestDetail present


if __name__ == "__main__":
    unittest.main()

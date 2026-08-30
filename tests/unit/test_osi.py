"""The OSI layers under MMS: framing, and that a PDU survives being wrapped and
unwrapped through Session/Presentation/ACSE."""

import unittest

from py61850.core import ber
from py61850.errors import TransportError
from py61850.mms import pdu
from py61850.osi import acse, presentation, session, stack, tpkt
from py61850.osi import cotp
from py61850.osi.cotp import CotpTransport


class FakeSocket:
    """Captures what CotpTransport writes; recv() is never used here."""

    def __init__(self):
        self.sent = b""

    def sendall(self, data):
        self.sent += data

    def frames(self):
        """The TPKT payloads (i.e. the COTP TPDUs) written so far."""
        out, i = [], 0
        while i < len(self.sent):
            n = tpkt.payload_len(self.sent[i:i + tpkt.HEADER_LEN])
            out.append(self.sent[i + tpkt.HEADER_LEN:i + tpkt.HEADER_LEN + n])
            i += tpkt.HEADER_LEN + n
        return out


class TestTpkt(unittest.TestCase):
    def test_wrap(self):
        self.assertEqual(tpkt.wrap(b"abc"), b"\x03\x00\x00\x07abc")

    def test_payload_len(self):
        self.assertEqual(tpkt.payload_len(tpkt.wrap(b"abc")[:4]), 3)

    def test_rejects_bad_version(self):
        with self.assertRaises(TransportError):
            tpkt.payload_len(b"\x04\x00\x00\x07")

    def test_rejects_short_header(self):
        with self.assertRaises(TransportError):
            tpkt.payload_len(b"\x03\x00")

    def test_rejects_impossible_length(self):
        with self.assertRaises(TransportError):
            tpkt.payload_len(b"\x03\x00\x00\x02")


class TestCotp(unittest.TestCase):
    def test_connection_request_bytes(self):
        """CR is built without any I/O, so it can be asserted directly."""
        t = CotpTransport("10.0.0.1")
        self.assertEqual(
            t.build_cr(),
            b"\x11"                       # LI = 17 bytes follow
            b"\xe0\x00\x00\x00\x01\x00"   # CR, dst-ref 0, src-ref 1, class 0
            b"\xc0\x01\x0a"               # TPDU size 2**10
            b"\xc1\x02\x00\x01"           # calling TSAP
            b"\xc2\x02\x00\x01")          # called TSAP

    def test_negotiated_size_defaults_to_what_we_proposed(self):
        self.assertEqual(CotpTransport("10.0.0.1").negotiated_tpdu_size, 1024)

    def test_cc_lowers_the_negotiated_size(self):
        """We propose 4096; the relay answers 1024 and that is what binds."""
        cc = b"\x0d\xd0\x00\x01\x00\x02\x00" b"\xc0\x01\x0a" b"\xc2\x02\x00\x01"
        self.assertEqual(cotp.negotiated_size_from(cc, 4096), 1024)

    def test_cc_cannot_raise_the_negotiated_size(self):
        """ISO 8073 lets the responder only lower the proposal, never raise it."""
        cc = b"\x09\xd0\x00\x01\x00\x02\x00" b"\xc0\x01\x0c"
        self.assertEqual(cotp.negotiated_size_from(cc, 1024), 1024)

    def test_cc_without_the_parameter_accepts_our_proposal(self):
        cc = b"\x06\xd0\x00\x01\x00\x02\x00"
        self.assertEqual(cotp.negotiated_size_from(cc, 1024), 1024)

    def test_split_dt_leaves_a_small_payload_in_one_tpdu(self):
        self.assertEqual(cotp.split_dt(b"abc", 1024), [b"\x02\xf0\x80abc"])

    def test_split_dt_still_sends_an_empty_payload(self):
        self.assertEqual(cotp.split_dt(b"", 1024), [b"\x02\xf0\x80"])

    def test_split_dt_fragments_with_eot_only_on_the_last(self):
        """A payload over the negotiated size must go out as a DT sequence --
        sending it as one oversized DT is what makes a relay drop the
        association, silently."""
        data = bytes(range(256)) * 12                      # 3072 bytes
        frames = cotp.split_dt(data, 1024)
        self.assertEqual(len(frames), 4)                   # 1021 * 3 + 9
        for f in frames[:-1]:
            self.assertEqual(f[:3], b"\x02\xf0\x00")      # EOT clear
            self.assertEqual(len(f), 1024)
        self.assertEqual(frames[-1][:3], b"\x02\xf0\x80")  # EOT set
        self.assertEqual(b"".join(f[3:] for f in frames), data)

    def test_exact_multiple_of_the_chunk_size_sets_eot_once(self):
        frames = cotp.split_dt(b"x" * (1024 - 3), 1024)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][2], 0x80)

    def test_send_emits_fragments_over_tpkt(self):
        t = CotpTransport("10.0.0.1")
        t.sock = FakeSocket()
        t.send(b"y" * 2000)
        frames = t.sock.frames()
        self.assertEqual(len(frames), 2)
        self.assertEqual(b"".join(f[3:] for f in frames), b"y" * 2000)

    def test_send_respects_a_lowered_negotiation(self):
        t = CotpTransport("10.0.0.1")
        t.sock = FakeSocket()
        t.negotiated_tpdu_size = 128
        t.send(b"z" * 500)
        self.assertEqual(len(t.sock.frames()), 4)          # 125 * 4

    def test_custom_tsaps(self):
        t = CotpTransport("10.0.0.1", called_tsap=b"\x00\x02", calling_tsap=b"\x00\x03")
        cr = t.build_cr()
        self.assertIn(b"\xc1\x02\x00\x03", cr)
        self.assertIn(b"\xc2\x02\x00\x02", cr)


class TestSession(unittest.TestCase):
    def test_data_prefix_round_trip(self):
        self.assertEqual(session.strip(session.wrap_data(b"payload")), b"payload")

    def test_connect_round_trip(self):
        self.assertEqual(session.strip(session.build_connect(b"pres")), b"pres")

    def test_accept_spdu(self):
        """An ACCEPT (0x0E) is walked the same way a CONNECT is."""
        params = b"\x05\x06\x13\x01\x00\x16\x01\x02" + b"\xc1\x04pres"
        accept = bytes([0x0E, len(params)]) + params
        self.assertEqual(session.strip(accept), b"pres")

    def test_empty(self):
        self.assertEqual(session.strip(b""), b"")


class TestPresentationAndAcse(unittest.TestCase):
    def test_user_data_round_trip(self):
        fed = presentation.wrap_user_data(presentation.CTX_MMS, b"\xa0\x01\x00")
        self.assertEqual(
            presentation.pdu_from_fully_encoded_data(
                presentation.find_fully_encoded_data(fed)),
            b"\xa0\x01\x00")

    def test_cp_declares_both_contexts(self):
        cp = presentation.build_cp(acse.build_aarq(b"\xa8\x00"))
        self.assertEqual(cp[0], presentation.CP_TYPE)
        # ctx 1 = ACSE, ctx 3 = MMS, both with BER as transfer syntax
        self.assertIn(b"\x02\x01\x01", cp)
        self.assertIn(b"\x02\x01\x03", cp)
        self.assertEqual(cp.count(b"\x06\x02\x51\x01"), 2)   # BER OID, once per context

    def test_acse_round_trip(self):
        mms = b"\xa8\x03\x80\x01\x01"
        self.assertEqual(acse.strip(acse.build_aarq(mms)), mms)

    def test_acse_strip_passes_through_bare_mms(self):
        self.assertEqual(acse.strip(b"\xa9\x01\x00"), b"\xa9\x01\x00")


class TestStack(unittest.TestCase):
    def test_associate_request_round_trip(self):
        """Session -> Presentation -> ACSE -> MMS, and back out again."""
        init = pdu.build_initiate()
        self.assertEqual(
            stack.extract_mms_from_response(stack.build_associate_request(init)),
            init)

    def test_data_phase_round_trip(self):
        request = pdu.confirmed_request(1, pdu.build_read("LD0", "X"))
        self.assertEqual(
            stack.extract_mms_from_response(stack.wrap_mms_pdu(request)), request)

    def test_payload_containing_0x61_is_not_mistaken_for_user_data(self):
        """fully-encoded-data is tag 0x61 -- and 'a' is also 0x61. A payload of
        'aaaa...' must be found structurally, not by scanning for the byte."""
        chunk = b"\x61" * 64
        service = ber.tlv(pdu.SVC_FILE_READ, ber.tlv(0x80, chunk))
        response = pdu.confirmed_response(2, service)
        extracted = stack.extract_mms_from_response(stack.wrap_mms_pdu(response))
        self.assertEqual(extracted, response)
        self.assertEqual(pdu.decode_file_read(pdu.service_from_response(extracted)),
                         (chunk, True))

    def test_missing_user_data_raises_transport_error(self):
        with self.assertRaises(TransportError):
            stack.extract_mms_from_response(b"\x01\x00\x01\x00")

    def test_garbage_raises_transport_error(self):
        with self.assertRaises(TransportError):
            stack.extract_mms_from_response(b"\x01\x00\x01\x00\x31\x02\xa2\x00")


if __name__ == "__main__":
    unittest.main()

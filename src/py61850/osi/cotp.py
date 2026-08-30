"""COTP / ISO 8073 class 0 over one TCP socket.

Two kinds of TPDU matter to us:
    * CR (Connection Request) / CC (Connection Confirm) for setup
    * DT (Data)                                        for payload

This is the only module in ``osi`` that touches a socket; everything above it
is pure bytes-in/bytes-out.
"""

import socket
import struct

from . import tpkt
from ..errors import TransportError

CR = 0xE0
CC = 0xD0
DT = 0xF0

DT_HEADER_LEN = 3          # LI + DT code + TPDU-NR/EOT octet
EOT = 0x80                 # end-of-TSDU bit in the TPDU-NR octet

PARAM_TPDU_SIZE = 0xC0     # variable parameter carrying the TPDU size code
DEFAULT_TPDU_SIZE = 0x0A   # 2**10 = 1024 bytes


# ---- pure TPDU helpers (no I/O -- reusable by a sniffer, ROADMAP 0.2) ----
def tpdu_size_of(code: int) -> int:
    """Decode a COTP TPDU-size parameter value: 0x07..0x0d -> 128..8192 bytes.

    Anything outside that range is not a size code; fall back to the class-0
    default rather than trusting it.
    """
    if 0x07 <= code <= 0x0D:
        return 1 << code
    return 1 << DEFAULT_TPDU_SIZE


def iter_parameters(tpdu: bytes):
    """Yield ``(code, value)`` for each variable parameter of a CR/CC TPDU.

    The fixed part of a CR/CC is 6 octets after the LI (type, dst-ref, src-ref,
    class/option), so the parameters run from offset 7 to the end of what the
    LI counts.
    """
    if len(tpdu) < 7:
        return
    end = min(1 + tpdu[0], len(tpdu))
    i = 7
    while i + 2 <= end:
        code, length = tpdu[i], tpdu[i + 1]
        if i + 2 + length > end:
            return
        yield code, bytes(tpdu[i + 2:i + 2 + length])
        i += 2 + length


def negotiated_size_from(tpdu: bytes, default: int) -> int:
    """The TPDU size the peer answered with in its CC, in bytes.

    ISO 8073 lets the responder only lower what we proposed, never raise it, so
    a peer that omits the parameter has accepted our proposal.
    """
    for code, value in iter_parameters(tpdu):
        if code == PARAM_TPDU_SIZE and value:
            return min(tpdu_size_of(value[0]), default)
    return default


def split_dt(data: bytes, max_tpdu: int):
    """Split an upper-layer payload into COTP DT TPDUs.

    A DT may not exceed the negotiated TPDU size, header included, so each
    fragment carries at most ``max_tpdu - 3`` octets of user data.  EOT is clear
    on every fragment but the last -- the mirror of what :meth:`CotpTransport.recv`
    reassembles.  An empty payload still produces one (empty, EOT-set) DT.
    """
    chunk = max(1, max_tpdu - DT_HEADER_LEN)
    out = []
    for start in range(0, max(len(data), 1), chunk):
        piece = data[start:start + chunk]
        last = start + chunk >= len(data)
        out.append(bytes([0x02, DT, EOT if last else 0x00]) + piece)
    return out


class CotpTransport:
    """A COTP class-0 connection over one TCP socket.

    src_ref / dst_ref are the COTP reference numbers. The TSAP selectors
    (called/calling) are carried as COTP parameters in the CR; most relays
    accept the common defaults but they are configurable here.
    """

    def __init__(self, host, port=102, src_ref=0x0001,
                 called_tsap=b"\x00\x01", calling_tsap=b"\x00\x01",
                 tpdu_size=0x0a, timeout=10):
        self.host = host
        self.port = port
        self.src_ref = src_ref
        self.called_tsap = called_tsap
        self.calling_tsap = calling_tsap
        self.tpdu_size = tpdu_size          # 0x0a => 1024 bytes (2**10)
        self.timeout = timeout
        self.sock = None
        # What the peer agreed to in its CC, in bytes. Set by connect(); until
        # then it is what we propose. Read-only for callers -- sizing a request
        # (mms.services.read) is the reason it is exposed at all.
        self.negotiated_tpdu_size = tpdu_size_of(tpdu_size)

    # ---- raw TPKT framing -------------------------------------------------
    def _send_tpkt(self, cotp_payload: bytes):
        try:
            self.sock.sendall(tpkt.wrap(cotp_payload))
        except OSError as e:
            raise TransportError(f"send failed: {e}") from e

    def _recv_exactly(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except OSError as e:
                raise TransportError(f"recv failed: {e}") from e
            if not chunk:
                raise TransportError("connection closed by peer")
            buf += chunk
        return buf

    def _recv_tpkt(self) -> bytes:
        """Read one full TPKT and return its COTP payload."""
        hdr = self._recv_exactly(tpkt.HEADER_LEN)
        return self._recv_exactly(tpkt.payload_len(hdr))

    # ---- COTP connection --------------------------------------------------
    def connect(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), self.timeout)
        except OSError as e:
            raise TransportError(f"cannot connect to {self.host}:{self.port}: {e}") from e
        self.sock.settimeout(self.timeout)
        # MMS is request/response over small PDUs, so Nagle would be the classic
        # latency trap here. Measured on an SEL-451 it was worth ~0.2 ms on a
        # single read and ~5 ms on an 8-container cycle -- not the win the shape
        # of the protocol suggests, but never a loss. Set it explicitly so the
        # next person does not have to re-measure.
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:                     # not fatal; some stacks refuse it
            pass
        self._send_tpkt(self.build_cr())

        resp = self._recv_tpkt()
        if len(resp) < 2:
            raise TransportError("short COTP response")
        if resp[1] & 0xF0 != CC:
            raise TransportError(f"expected CC(0x{CC:02x}), got 0x{resp[1]:02x}")
        self.negotiated_tpdu_size = negotiated_size_from(
            resp, tpdu_size_of(self.tpdu_size))
        return resp

    def build_cr(self) -> bytes:
        """Build the COTP Connection Request TPDU (no I/O)."""
        params = b""
        params += bytes([0xC0, 0x01, self.tpdu_size])                  # TPDU size (0xC0)
        params += bytes([0xC1, len(self.calling_tsap)]) + self.calling_tsap  # Calling TSAP
        params += bytes([0xC2, len(self.called_tsap)]) + self.called_tsap    # Called TSAP

        fixed = struct.pack("!BHHB",
                            CR,
                            0x0000,        # dst-ref
                            self.src_ref,  # src-ref
                            0x00)          # class 0, no options
        cr = fixed + params
        return bytes([len(cr)]) + cr       # length indicator = everything after LI byte

    # ---- COTP data transfer ----------------------------------------------
    def send(self, data: bytes):
        """Send an upper-layer payload as one or more COTP DT TPDUs.

        Anything longer than the negotiated TPDU size goes out fragmented, EOT
        set only on the last one. Sending it as a single oversized DT is what a
        relay answers by dropping the association -- with no MMS error, so the
        failure surfaces later as a closed socket.
        """
        # DT TPDU: LI=2, type 0xF0, TPDU-NR|EOT
        for frame in split_dt(data, self.negotiated_tpdu_size):
            self._send_tpkt(frame)

    def recv(self) -> bytes:
        """Receive one upper-layer payload, reassembling COTP DT fragments."""
        out = b""
        while True:
            cotp = self._recv_tpkt()
            li = cotp[0]
            if cotp[1] & 0xF0 != DT:
                raise TransportError(f"expected DT(0x{DT:02x}), got 0x{cotp[1]:02x}")
            eot = cotp[2] & 0x80
            out += cotp[1 + li:]          # data starts after the LI-counted header
            if eot:
                return out

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

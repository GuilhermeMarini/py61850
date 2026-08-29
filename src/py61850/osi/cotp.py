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
        self._send_tpkt(self.build_cr())

        resp = self._recv_tpkt()
        if len(resp) < 2:
            raise TransportError("short COTP response")
        if resp[1] & 0xF0 != CC:
            raise TransportError(f"expected CC(0x{CC:02x}), got 0x{resp[1]:02x}")
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
        """Send an upper-layer payload as a COTP DT TPDU (EOT set)."""
        # DT TPDU: LI=2, type 0xF0, TPDU-NR|EOT = 0x80
        self._send_tpkt(bytes([0x02, DT, 0x80]) + data)

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

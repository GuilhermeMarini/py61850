"""Minimal definite-length BER encoder/decoder.

MMS PDUs are ASN.1 encoded with the Basic Encoding Rules (definite length), and
so are the GOOSE APDU and the SV ``savPdu`` envelope -- so this module is the
bottom of all three protocol stacks.  We only need TLV building blocks: encode a
length, wrap tag+len+value, and walk a buffer of TLVs.  Tags are passed as raw
integers; multi-byte (high-tag-number) tags fold into one integer, so the file
services' ``[72]`` is written 0xBF48.

Two decode APIs
---------------
``read_tlv`` / ``iter_tlv`` copy each value into a fresh ``bytes``.  That is the
convenient form and what the MMS layer uses -- a scan is a few hundred PDUs and
the copies are free.

``read_tlv_at`` / ``iter_tlv_view`` hand back offsets and memoryviews into the
original buffer instead.  A Sampled Values subscriber decodes thousands of
frames a second; at that rate the per-TLV copy is the cost.  Both APIs share one
implementation (``read_tlv_at``), so there is no second parser to keep correct.
"""


# ---- encoding ------------------------------------------------------------
def enc_len(n: int) -> bytes:
    """Encode a definite length (short form < 128, else long form)."""
    if n < 0x80:
        return bytes([n])
    out = b""
    while n:
        out = bytes([n & 0xFF]) + out
        n >>= 8
    return bytes([0x80 | len(out)]) + out


def enc_tag(tag: int) -> bytes:
    """Encode identifier octets. Single-byte tags (<=0xFF) pass through; larger
    values are treated as pre-composed multi-byte tags (e.g. 0xBF48 -> BF 48)."""
    if tag <= 0xFF:
        return bytes([tag])
    out = b""
    while tag:
        out = bytes([tag & 0xFF]) + out
        tag >>= 8
    return out


def tlv(tag: int, value: bytes) -> bytes:
    """tag (1+ bytes) + length + value."""
    return enc_tag(tag) + enc_len(len(value)) + value


def enc_uint(n: int) -> bytes:
    """Encode an unsigned integer as minimal big-endian bytes (>=1 byte).

    A leading 0x00 is prepended when the top bit is set, so the value is not
    misread as negative (BER INTEGER is signed).
    """
    if n == 0:
        return b"\x00"
    out = b""
    while n:
        out = bytes([n & 0xFF]) + out
        n >>= 8
    if out[0] & 0x80:
        out = b"\x00" + out
    return out


def enc_int(n: int) -> bytes:
    """Encode a signed integer as minimal two's-complement big-endian bytes."""
    if n == 0:
        return b"\x00"
    # Minimal two's-complement width. For negatives the boundary is asymmetric:
    # -128 fits in one octet but (-128).bit_length() is 8, so measure -n-1.
    bits = (n if n > 0 else -n - 1).bit_length()
    return n.to_bytes(bits // 8 + 1, "big", signed=True)


def int_tlv(tag: int, n: int) -> bytes:
    return tlv(tag, enc_uint(n))


# ---- decoding ------------------------------------------------------------
def read_len(buf, i: int):
    """Return (length, index_after_length)."""
    b0 = buf[i]
    i += 1
    if b0 < 0x80:
        return b0, i
    num = b0 & 0x7F
    length = int.from_bytes(buf[i:i + num], "big")
    return length, i + num


def read_tag(buf, i: int):
    """Return (tag_int, index_after_tag). High-tag-number form (low 5 bits set)
    is folded into one integer, so 0xBF 0x48 comes back as 0xBF48."""
    first = buf[i]
    j = i + 1
    tag = first
    if (first & 0x1F) == 0x1F:               # multi-byte tag
        while True:
            b = buf[j]
            tag = (tag << 8) | b
            j += 1
            if not (b & 0x80):
                break
    return tag, j


def read_tlv_at(buf, i: int = 0):
    """Zero-copy core parser: return (tag, value_start, value_end, next_index).

    Nothing is sliced -- callers index into ``buf`` themselves.  Every other
    decode helper in this module is written in terms of this one.
    """
    tag, j = read_tag(buf, i)
    length, k = read_len(buf, j)
    return tag, k, k + length, k + length


def read_tlv(buf, i: int = 0):
    """Return (tag, value_bytes, next_index). Copies the value."""
    tag, start, end, nxt = read_tlv_at(buf, i)
    return tag, bytes(buf[start:end]), nxt


def iter_tlv(buf):
    """Yield (tag, value_bytes) for each TLV in a buffer of concatenated TLVs."""
    i = 0
    n = len(buf)
    while i < n:
        tag, start, end, i = read_tlv_at(buf, i)
        yield tag, bytes(buf[start:end])


def iter_tlv_view(buf):
    """Yield (tag, memoryview) for each TLV -- no copies.

    The views alias ``buf``; do not hold them past the lifetime of the frame.
    Use this on the GOOSE/SV hot path, ``iter_tlv`` everywhere else.
    """
    mv = buf if isinstance(buf, memoryview) else memoryview(buf)
    i = 0
    n = len(mv)
    while i < n:
        tag, start, end, i = read_tlv_at(mv, i)
        yield tag, mv[start:end]


def to_int(value) -> int:
    return int.from_bytes(value, "big")

"""ISO 8823 Presentation layer.

Association carries a CP-type (a SET, tag 0x31) that declares the presentation
context list -- context 1 = ACSE, context 3 = MMS -- and embeds the AARQ.  In
the data phase the presentation layer shrinks to fully-encoded-data (0x61)
tagging each PDU with the context it belongs to.
"""

from ..core import ber
from . import oids

CP_TYPE = 0x31
FULLY_ENCODED_DATA = 0x61
SINGLE_ASN1_TYPE = 0xA0

CTX_ACSE = 1
CTX_MMS = 3


def _context_item(ctx_id: int, abstract_oid: bytes) -> bytes:
    item = ber.int_tlv(0x02, ctx_id)                     # presentation-context-identifier
    item += oids.oid_tlv(abstract_oid)                   # abstract-syntax-name
    item += ber.tlv(0x30, oids.oid_tlv(oids.BER))        # transfer-syntax-name-list {BER}
    return ber.tlv(0x30, item)


def wrap_user_data(ctx_id: int, pdu: bytes) -> bytes:
    """fully-encoded-data [APPLICATION 1] { PDV-list { ctx-id, single-ASN1-type } }."""
    pdv = ber.int_tlv(0x02, ctx_id)
    pdv += ber.tlv(SINGLE_ASN1_TYPE, pdu)
    return ber.tlv(FULLY_ENCODED_DATA, ber.tlv(0x30, pdv))


def build_cp(aarq: bytes, calling_psel=b"\x00\x00\x00\x01",
             called_psel=b"\x00\x00\x00\x01") -> bytes:
    mode = ber.tlv(0xA0, ber.int_tlv(0x80, 1))           # mode-selector = normal(1)

    ctx_list = _context_item(CTX_ACSE, oids.ACSE_AS)
    ctx_list += _context_item(CTX_MMS, oids.MMS_AS)

    normal = b""
    normal += ber.tlv(0x81, calling_psel)                # calling-presentation-selector [1]
    normal += ber.tlv(0x82, called_psel)                 # called-presentation-selector  [2]
    normal += ber.tlv(0xA4, ctx_list)                    # context-definition-list [4]
    normal += wrap_user_data(CTX_ACSE, aarq)             # the AARQ, under ACSE context 1
    return ber.tlv(CP_TYPE, mode + ber.tlv(0xA2, normal))


def find_fully_encoded_data(pres: bytes):
    """Return the value of fully-encoded-data (0x61), descending presentation
    containers (CPA-type 0x31, normal-mode-params 0xA2) but nothing else.

    Parsed structurally, NOT by byte-scanning: file and read payloads can
    contain any byte, 0x61 included.
    """
    try:
        for tag, val in ber.iter_tlv(pres):
            if tag == FULLY_ENCODED_DATA:
                return val
            if tag in (CP_TYPE, 0xA2, 0xA0):     # descend presentation structure only
                r = find_fully_encoded_data(val)
                if r is not None:
                    return r
    except (IndexError, ValueError):
        return None
    return None


def pdu_from_fully_encoded_data(fed: bytes):
    """fed = fully-encoded-data value = SEQUENCE{ PDV-list }; return the
    single-ASN1-type [0] contents (the MMS or ACSE PDU)."""
    try:
        _, seq, _ = ber.read_tlv(fed, 0)      # 0x30 PDV-list
        for tag, val in ber.iter_tlv(seq):
            if tag == SINGLE_ASN1_TYPE:
                return val
    except (IndexError, ValueError):
        return None
    return None

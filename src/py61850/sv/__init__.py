"""Sampled Values -- IEC 61850-9-2 Layer-2 streaming.

**Planned; nothing implemented yet.** Follows GOOSE (ROADMAP 2.0) -- once the
L2 machinery in :mod:`py61850.link` exists, SV is the same machinery at a
different EtherType (0x88BA).

    pdu         savPdu envelope (BER, via :mod:`py61850.core.ber`) around ASDUs
    subscriber  decode a live stream
    publisher   transmit a stream

SV is the reason :mod:`py61850.core.ber` carries a zero-copy decode path
(``read_tlv_at`` / ``iter_tlv_view``): at 4000 ASDUs/second the per-TLV copy in
the convenience API is the dominant cost.
"""

__all__ = []

"""Protocol-agnostic building blocks shared by MMS, GOOSE and SV.

Nothing in this package performs I/O: it is pure encode/decode over bytes.
That is what lets the same code serve a client and a server (MMS), and a
subscriber and a publisher (GOOSE/SV), and be unit-tested offline.

    ber       definite-length BER TLV encode/decode
    data      MMS ``Data`` values -- the CHOICE reused verbatim by GOOSE
              ``allData`` and inside the SV ``savPdu`` envelope
    quality   the IEC 61850 13-bit Quality bitstring
    time      MMS ``UtcTime`` / ``BinaryTime``

Import these from ``py61850.core.<module>``; they are internal to the package
and may change between releases.
"""

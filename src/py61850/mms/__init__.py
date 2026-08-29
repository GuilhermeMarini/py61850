"""MMS (ISO 9506) -- the confirmed-service protocol IEC 61850 maps ACSI onto.

    pdu        every request builder and response decoder, with no I/O at all
    types      TypeDescription (GetVariableAccessAttributes result)
    service_error   confirmed-ErrorPDU decoding
    client     MmsClient / FileTransfer -- association, transactions, sockets
    services/  one mixin per service group, composed onto the client

The split between ``pdu`` and ``client`` is deliberate: a request builder and a
response decoder are the same code a *server* needs, only used in the opposite
direction, and code with no sockets in it can be tested against recorded PDUs
offline.  Keep ``pdu`` socket-free.
"""

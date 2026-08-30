"""GetDataValues -> MMS Read.

One Read may name many variables, so a poll loop that wants N values should
cost one request, not N.  :meth:`ReadMixin.read_many` batches them, splitting
into as many requests as the association's negotiated MMS PDU size needs.

Batching, not pipelining, is the lever here.  Writing several requests before
reading their responses was measured on an SEL-451 at ~3x *slower* (the
delayed-ACK signature), even though the relay advertises
``maxServOutstanding=3``; see ROADMAP "Cross-cutting".
"""

from .. import pdu

# Room left in a request for the confirmed-request envelope (invokeID, service
# and varAccessSpec headers) plus the Session/Presentation wrap around it.
# Generous on purpose: overshooting the server's PDU limit is not an error the
# relay reports, it just drops the association.
_REQUEST_OVERHEAD = 128


class ReadMixin:
    def read(self, ld, item):
        """Read -> raw MMS Data value TLV bytes (see core.data to decode)."""
        return self._transact(pdu.build_read(ld, item))

    def read_value(self, ld, item):
        """Read and decode one variable into a Python value.

        Convenience wrapper over :meth:`read` + ``decode_read_response``.
        Returns the single decoded value (or ``None`` if the relay returned
        nothing). A failed access comes back as ``{"error": <code-name>}``.
        Use :meth:`read` directly when you need the raw TLV bytes.
        """
        results = pdu.decode_read_response(self.read(ld, item))
        return results[0] if results else None

    def read_many(self, ld, items):
        """Read many variables of one logical device, decoded, in order.

            c.read_many("MYLD_ANN", ["ACN1GGIO1$ST$Ind1$stVal",
                                     "ACN1GGIO1$ST$Ind2$stVal"])

        Costs one request per batch that fits the negotiated PDU size, not one
        per variable -- the whole point of the call. Each failed access appears
        in place as ``{"error": <code-name>}``, so the result is always as long
        as ``items``.
        """
        items = list(items)
        values = []
        for group in self._read_batches(ld, items):
            values.extend(pdu.decode_read_response(
                self._transact(pdu.build_read_multi(ld, group))))
        return values

    def read_data_set(self, ld, name):
        """Read a whole named variable list (DataSet) in one request.

            c.read_data_set("MYLD_ANN", "BRDSet01")

        The set is read by name, so the server decides its members; use
        :meth:`~py61850.mms.services.directory.DirectoryMixin.get_data_set_directory`
        to list the sets an LD offers. Values come back decoded and in the
        server's member order.
        """
        return pdu.decode_read_response(
            self._transact(pdu.build_read_named_list(ld, name)))

    # ---- batching ---------------------------------------------------------
    def _read_batches(self, ld, items):
        """Split ``items`` into groups whose Read request fits one MMS PDU.

        The budget is the *server's* limit (``max_pdu_size``, its
        localDetailCalled) -- the COTP TPDU size is a separate and usually
        smaller ceiling, but CotpTransport fragments across it on its own.
        """
        budget = max(self.max_pdu_size - _REQUEST_OVERHEAD, 1)
        group, used = [], 0
        for item in items:
            size = len(pdu.read_entry(ld, item))
            if group and used + size > budget:
                yield group
                group, used = [], 0
            group.append(item)
            used += size
        if group:
            yield group

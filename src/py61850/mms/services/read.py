"""GetDataValues -> MMS Read."""

from .. import pdu


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

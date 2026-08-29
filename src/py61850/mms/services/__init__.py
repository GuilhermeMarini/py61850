"""One mixin per MMS service group, composed onto the client in
:mod:`py61850.mms.client`.

Each mixin is I/O-free except for calling ``self._transact(service_bytes)``,
which the client base provides: build a request with :mod:`py61850.mms.pdu`,
send it, decode the reply with :mod:`py61850.mms.pdu`.

Mixins rather than a subclass chain, because service groups compose.  A client
that needs files *and* reports *and* control is one class listing three mixins;
with ``FileTransfer(MmsClient)``-style inheritance it would be a diamond.
Write (SetDataValues), reports (RCB) and control are the ones ROADMAP 0.2 adds.
"""

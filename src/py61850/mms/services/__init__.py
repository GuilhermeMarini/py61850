# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
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

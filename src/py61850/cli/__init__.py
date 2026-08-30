# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Command-line drivers.

Installed as ``py61850`` (subcommands), plus ``mms-scan`` / ``mms-files`` for
the two MMS drivers directly.

This is the only layer allowed to ``print`` or ``sys.exit``: the library under
``core``/``osi``/``mms`` returns values and raises exceptions.
"""

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The IEC 61850-7-2 functional constraints.

An FC says what a data attribute *is for* -- a status, a measurement, a
setting, a command -- and it appears in two places that must agree: the
``fc`` attribute of a ``DA`` in an SCL file, and the middle segment of an
MMS item name (``LN$ST$Pos$stVal``).  That is why this lives in ``core``
rather than under ``scl``: a client matching an item against
``GetLogicalDeviceDirectory`` needs the same vocabulary as a reader walking
a file, and must not have to import the SCL package to get it.

The same module answers the same question from the other direction.  An FC
says a whole container is a command; the control model of 61850-7-2 says
which *attributes* of a controllable data object carry one.  Both are needed,
because the two are not always available together -- see
:func:`is_control_attribute`.
"""

from __future__ import annotations

from .refs import da_parts

#: Every functional constraint IEC 61850-7-2 defines.
#:
#: The first twelve were measured across the three reference SCDs (7.1, 13.5
#: and 23.5 MB, SEL and Siemens): ``ST SR CF EX SE DC MX OR CO SV SP BL``.
#: ``SG`` (setting group) completes the standard's set and is kept even
#: though that corpus does not use it -- a vocabulary with a hole in it fails
#: silently on the first file that fills it.
FUNCTIONAL_CONSTRAINTS = (
    "ST",   # status
    "MX",   # measurand
    "SP",   # setpoint
    "SV",   # substitution
    "CF",   # configuration
    "DC",   # description
    "SG",   # setting group
    "SE",   # setting group editable
    "SR",   # service response / service tracking
    "OR",   # operate received
    "BL",   # blocking
    "EX",   # extended definition
    "CO",   # control
)

#: The FCs that carry a command rather than a reading.
CONTROL_FCS = frozenset({"CO"})

#: The data attributes of the 61850-7-2 control model: the ones through which
#: a controllable data object (SPC, DPC, INC, ENC, BSC, ISC, APC, BAC) is
#: COMMANDED rather than read.  They are the attribute-name counterpart of
#: ``CONTROL_FCS``, and they are not redundant with it: an attribute path is
#: often in hand when its FC is not.  A live client resolving a name against
#: ``GetLogicalDeviceDirectory``, or any consumer holding an item name whose
#: middle segment it has not yet parsed, has only the spelling to go on.
CONTROL_DATA_ATTRIBUTES = frozenset({"Oper", "SBOw", "SBO", "Cancel"})

# Ordered best-to-worst for "if this attribute is reachable under several FCs,
# which one should be read?".  Status first, then measurand, then the settings
# and descriptive constraints.  Controls are absent on purpose -- they are
# ranked by `read_rank` below, strictly last, and never by position here.
_READ_PREFERENCE = ("ST", "MX", "SP", "CF", "DC", "SR", "OR", "EX", "SV",
                    "SG", "SE", "BL")


def _norm(value) -> str:
    return value.upper() if isinstance(value, str) else ""


def is_valid(fc) -> bool:
    """Is this one of the 13 functional constraints 7-2 defines?"""
    return _norm(fc) in FUNCTIONAL_CONSTRAINTS


def is_control(fc) -> bool:
    """Does this FC carry a command rather than a reading?"""
    return _norm(fc) in CONTROL_FCS


def read_rank(fc) -> tuple:
    """Sort key for one candidate FC; lower is a better thing to poll.

    ``(0, position)`` for anything that is not a control, ``(1, 0)`` for one
    that is.  The two tiers are deliberate: they keep a control strictly worse
    than *every* reading, including an FC this table has never heard of.  A
    command SETS a point, so polling it returns what the device was last told
    rather than what it sees, and that is worse than an unfamiliar reading.
    """
    name = _norm(fc)
    if name in CONTROL_FCS:
        return (1, 0)
    if name in _READ_PREFERENCE:
        return (0, _READ_PREFERENCE.index(name))
    return (0, len(_READ_PREFERENCE))


def is_control_attribute(path) -> bool:
    """Does this attribute path address a command rather than a reading?

    Takes either spelling of the descent -- ``"Oper.ctlVal"`` as SCL writes
    it, ``"Oper$ctlVal"`` as MMS does, or the parts already split.

    Two rules, and the second is not covered by the first.  The path is a
    command when it descends through one of :data:`CONTROL_DATA_ATTRIBUTES`,
    which is the control model's own vocabulary; and also when its leaf is a
    ``ctlVal``, because ``ctlVal`` appears only inside a control and a
    consumer may hold the leaf without the root that carried it.  The leaf
    test is a prefix match: 7-3 spells the analogue control value
    ``ctlVal`` on its own but attaches the setpoint variants beside it, and
    a name that begins ``ctlVal`` is a control value in every one of them.
    """
    parts = da_parts(path)
    if not parts:
        return False
    return parts[0] in CONTROL_DATA_ATTRIBUTES or parts[-1].startswith("ctlVal")

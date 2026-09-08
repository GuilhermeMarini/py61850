# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""IEC 61850-8-1 naming: object reference <-> MMS domain and item name.

The same data attribute has two spellings, and a tool that reads files and
talks to devices needs both::

    object reference   QPC1PRO/PTRC1.Op.general      (61850-6, 61850-7-2)
    MMS domain + item  QPC1PRO  PTRC1$ST$Op$general  (61850-8-1)

The mapping is mechanical but not obvious: the functional constraint sits
*between* the logical node and the data object in the MMS spelling and does
not appear in the object reference at all, and the MMS domain is the LDevice's
``ldName`` when it has one, or ``iedName`` concatenated with ``inst`` when it
does not.

This is in ``core`` because it belongs to neither side exclusively: an SCL
reader builds these names from a file, and an MMS client parses them back out
of ``GetNameList``.
"""

from __future__ import annotations


def ld_name(ied_name: str, ld_inst: str, ld_name_attr=None) -> str:
    """The MMS domain of one LDevice.

    ``ldName`` when the file gives one -- 61850-6 allows an LDevice to name
    itself outright, and then that name IS the domain. Otherwise the 8-1
    default: the IED's name with the LDevice's ``inst`` appended.
    """
    explicit = (ld_name_attr or "").strip()
    if explicit:
        return explicit
    return f"{ied_name or ''}{ld_inst or ''}"


def ln_name(prefix, ln_class, inst) -> str:
    """The MMS spelling of one logical node: prefix + class + instance.

    ``LN0`` is spelled ``LLN0`` by its ``lnClass``, so no special case is
    needed here -- the caller passes what the element carries.
    """
    return f"{prefix or ''}{ln_class or ''}{inst or ''}"


def mms_item(ln: str, fc: str, path) -> str:
    """``PTRC1``, ``ST``, ``["Op", "general"]`` -> ``PTRC1$ST$Op$general``.

    ``path`` is the descent from the data object down to the leaf attribute,
    one entry per level: ``["Pos", "Oper", "ctlVal"]`` for a control.
    """
    return "$".join([ln, fc] + [str(p) for p in path])


def object_reference(ld: str, ln: str, path) -> str:
    """``QPC1PRO``, ``PTRC1``, ``["Op", "general"]``
    -> ``QPC1PRO/PTRC1.Op.general``.

    The functional constraint is absent by design: an object reference names
    the object, and the FC says how it is being accessed.
    """
    return f"{ld}/{ln}." + ".".join(str(p) for p in path)


def split_item(item: str):
    """``PTRC1$ST$Op$general`` -> ``("PTRC1", "ST", ("Op", "general"))``.

    ``None`` for anything that is not an attribute item -- a bare logical
    node name, or an empty string. Refusing is deliberate: inventing an FC
    for a name that carries none produces an item no device serves, and the
    failure then surfaces far downstream as a silent missing value.
    """
    parts = [p for p in (item or "").split("$") if p]
    if len(parts) < 3:
        return None
    return parts[0], parts[1], tuple(parts[2:])


def da_parts(da) -> tuple:
    """``"Oper.ctlVal"`` / ``"Oper$ctlVal"`` -> ``("Oper", "ctlVal")``.

    SCL writes the descent through an SDI with ``.``; MMS spells every level
    with ``$``. A tool reading both needs one canonical form, and empty
    segments are dropped rather than preserved.
    """
    if not isinstance(da, str):
        return tuple(da) if da else ()
    return tuple(p for p in da.replace("$", ".").split(".") if p)

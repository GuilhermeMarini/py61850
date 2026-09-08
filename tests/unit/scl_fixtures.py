# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Small SCL documents, built in Python so a test shows the exact XML it
depends on.

``tests/fixtures/`` holds recorded MMS bytes off real hardware; SCL test
material is synthetic and belongs here instead, where it can be read beside
the assertion it supports.
"""

SCL_NS = "http://www.iec.ch/61850/2003/SCL"


def scl(*sections, **kwargs):
    """An `<SCL>` document containing `sections`, as a text string.

    ``ns=False`` drops the namespace declaration entirely -- hand-made SCDs
    that declare none are real, and every reader here must survive them.
    """
    ns = kwargs.pop("ns", True)
    assert not kwargs, kwargs
    decl = f' xmlns="{SCL_NS}"' if ns else ""
    body = "\n".join(sections)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<SCL{decl}>\n{body}\n</SCL>\n'


def header(id_="ST1", version="2007", revision="B", tool_id="test"):
    return (f'<Header id="{id_}" version="{version}" revision="{revision}" '
            f'toolID="{tool_id}" nameStructure="IEDName"/>')


def private(type_, text=""):
    return f'<Private type="{type_}">{text}</Private>'


def ied(name, body="", **attrs):
    """An `<IED>` element. `attrs` become XML attributes verbatim."""
    extra = "".join(f' {k}="{v}"' for k, v in sorted(attrs.items()))
    return f'<IED name="{name}"{extra}>\n{body}\n</IED>'


def write(tmpdir, name, text):
    """Write `text` to `tmpdir/name` as UTF-8 and return the path."""
    import os
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def templates(*types):
    """A `<DataTypeTemplates>` section containing `types`."""
    return "<DataTypeTemplates>\n" + "\n".join(types) + "\n</DataTypeTemplates>"


def lnode_type(id_, ln_class="LLN0", dos=(), body=""):
    """`dos` is a sequence of (DO name, DOType id)."""
    inner = "".join(f'<DO name="{n}" type="{t}"/>' for n, t in dos)
    return f'<LNodeType id="{id_}" lnClass="{ln_class}">{inner}{body}</LNodeType>'


def do_type(id_, cdc="SPS", das=(), sdos=(), body=""):
    """`das` is a sequence of dicts of DA attributes; `sdos` of (name, type)."""
    inner = "".join(
        "<DA" + "".join(f' {k}="{v}"' for k, v in sorted(d.items())) + "/>"
        for d in das)
    inner += "".join(f'<SDO name="{n}" type="{t}"/>' for n, t in sdos)
    return f'<DOType id="{id_}" cdc="{cdc}">{inner}{body}</DOType>'


def da_type(id_, bdas=()):
    """`bdas` is a sequence of dicts of BDA attributes."""
    inner = "".join(
        "<BDA" + "".join(f' {k}="{v}"' for k, v in sorted(d.items())) + "/>"
        for d in bdas)
    return f'<DAType id="{id_}">{inner}</DAType>'


def enum_type(id_, values=()):
    """`values` is a sequence of (ord, text)."""
    inner = "".join(f'<EnumVal ord="{o}">{t}</EnumVal>' for o, t in values)
    return f'<EnumType id="{id_}">{inner}</EnumType>'


def address(**params):
    """`<Address>` with one `<P type=...>` per keyword. Use `P_IP=...` style
    keys; underscores in the key become dashes in the type."""
    inner = "".join(f'<P type="{k.replace("_", "-")}">{v}</P>'
                    for k, v in sorted(params.items()))
    return f"<Address>{inner}</Address>"


def gse(ld_inst, cb_name, addr="", min_time=None, max_time=None):
    times = ""
    if min_time is not None:
        times += f'<MinTime unit="s" multiplier="m">{min_time}</MinTime>'
    if max_time is not None:
        times += f'<MaxTime unit="s" multiplier="m">{max_time}</MaxTime>'
    return f'<GSE ldInst="{ld_inst}" cbName="{cb_name}">{addr}{times}</GSE>'


def smv(ld_inst, cb_name, addr=""):
    return f'<SMV ldInst="{ld_inst}" cbName="{cb_name}">{addr}</SMV>'


def connected_ap(ied_name, ap_name="S1", body=""):
    return (f'<ConnectedAP iedName="{ied_name}" apName="{ap_name}">'
            f'{body}</ConnectedAP>')


def subnetwork(name, aps=(), type_="8-MMS"):
    return (f'<SubNetwork name="{name}" type="{type_}">'
            + "".join(aps) + "</SubNetwork>")


def communication(*subnets):
    return "<Communication>" + "".join(subnets) + "</Communication>"

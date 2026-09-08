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

    ``version``/``revision``/``release`` set the SCHEMA EDITION attributes on
    the ``<SCL>`` root itself, as the standard puts them -- real files carry
    ``version="2007" revision="B" release="4"`` there, quite apart from
    whatever bookkeeping ``<Header>`` carries. All three default to unset, so
    a test that does not care about the edition gets none.
    """
    ns = kwargs.pop("ns", True)
    version = kwargs.pop("version", None)
    revision = kwargs.pop("revision", None)
    release = kwargs.pop("release", None)
    assert not kwargs, kwargs
    decl = f' xmlns="{SCL_NS}"' if ns else ""
    root_attrs = ""
    if version is not None:
        root_attrs += f' version="{version}"'
    if revision is not None:
        root_attrs += f' revision="{revision}"'
    if release is not None:
        root_attrs += f' release="{release}"'
    body = "\n".join(sections)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<SCL{decl}{root_attrs}>'
            f'\n{body}\n</SCL>\n')


def header(id_="ST1", version="1", revision="1.0", tool_id="test"):
    """A ``<Header>``. Defaults are BOOKKEEPING-shaped, as real files' are --
    an exporting tool's own version/revision, not the schema edition. On the
    reference corpus ``Header@version``/``@revision`` hold values like
    ``"204"``/``"1.0"`` (SEL) and ``"1"``/``"199"`` (Siemens), unrelated to
    the ``2007``/``B`` every one of those files carries on the ``<SCL>``
    root; see :func:`scl` for setting that.
    """
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


def ln(ln_class, inst="1", prefix="", ln_type="T", body=""):
    return (f'<LN lnClass="{ln_class}" inst="{inst}" prefix="{prefix}" '
            f'lnType="{ln_type}">{body}</LN>')


def ln0(ln_type="T_LLN0", body=""):
    return f'<LN0 lnClass="LLN0" inst="" lnType="{ln_type}">{body}</LN0>'


def ldevice(inst, body="", **attrs):
    extra = "".join(f' {k}="{v}"' for k, v in sorted(attrs.items()))
    return f'<LDevice inst="{inst}"{extra}>{body}</LDevice>'


def access_point(name="S1", body="", server=True, lns=""):
    """An `<AccessPoint>`. `lns` goes BESIDE the Server, not inside it --
    that is where 61850-6 puts a gateway's proxy LNs."""
    inner = f"<Server>{body}</Server>" if server else body
    return f'<AccessPoint name="{name}">{inner}{lns}</AccessPoint>'


def dai(name, val=None, s_addr=None, body=""):
    attrs = f' name="{name}"'
    if s_addr is not None:
        attrs += f' sAddr="{s_addr}"'
    inner = body + (f"<Val>{val}</Val>" if val is not None else "")
    return f"<DAI{attrs}>{inner}</DAI>"


def sdi(name, body=""):
    return f'<SDI name="{name}">{body}</SDI>'


def doi(name, body=""):
    return f'<DOI name="{name}">{body}</DOI>'


def fcda(ld_inst, ln_class, do_name, fc, prefix="", ln_inst="", da_name=""):
    return (f'<FCDA ldInst="{ld_inst}" prefix="{prefix}" lnClass="{ln_class}" '
            f'lnInst="{ln_inst}" doName="{do_name}" daName="{da_name}" '
            f'fc="{fc}"/>')


def dataset(name, fcdas=(), desc=""):
    return (f'<DataSet name="{name}" desc="{desc}">' + "".join(fcdas)
            + "</DataSet>")


def gse_control(name, dat_set, app_id="", conf_rev="1", body=""):
    return (f'<GSEControl name="{name}" datSet="{dat_set}" appID="{app_id}" '
            f'confRev="{conf_rev}" type="GOOSE">{body}</GSEControl>')


def report_control(name, dat_set, conf_rev="1", body=""):
    return (f'<ReportControl name="{name}" datSet="{dat_set}" '
            f'confRev="{conf_rev}">{body}</ReportControl>')


def smv_control(name, dat_set, app_id="4000"):
    return (f'<SampledValueControl name="{name}" datSet="{dat_set}" '
            f'smvID="{name}" appID="{app_id}"/>')


def setting_control(num_of_sgs="6", act_sg="1"):
    return f'<SettingControl numOfSGs="{num_of_sgs}" actSG="{act_sg}"/>'


def ext_ref(**attrs):
    return "<ExtRef" + "".join(f' {k}="{v}"' for k, v in sorted(attrs.items())) + "/>"


def inputs(*refs):
    return "<Inputs>" + "".join(refs) + "</Inputs>"

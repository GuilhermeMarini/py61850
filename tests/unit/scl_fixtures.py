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

    ``xsi=True`` declares the ``XMLSchema-instance`` prefix on the root, which
    a document has to do before any `P` may carry an `xsi:type`. Two of the
    three corpus files declare it through their ``xsi:schemaLocation``;
    `sel.scd` declares no such prefix at all, which is why it is off by
    default here.

    ``version``/``revision``/``release`` set the SCHEMA EDITION attributes on
    the ``<SCL>`` root itself, as the standard puts them -- real files carry
    ``version="2007" revision="B" release="4"`` there, quite apart from
    whatever bookkeeping ``<Header>`` carries. All three default to unset, so
    a test that does not care about the edition gets none.
    """
    ns = kwargs.pop("ns", True)
    xsi = kwargs.pop("xsi", False)
    version = kwargs.pop("version", None)
    revision = kwargs.pop("revision", None)
    release = kwargs.pop("release", None)
    assert not kwargs, kwargs
    decl = f' xmlns="{SCL_NS}"' if ns else ""
    if xsi:
        decl += ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
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


def comment(text=" note "):
    """An XML comment. `text` goes between the delimiters verbatim.

    A default with spaces around it, because that is how every file in the
    reference corpus writes one and the round trip has to reproduce the
    spacing as well as the words.
    """
    assert "--" not in text, "a comment may not contain --"
    return f"<!--{text}-->"


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


def address(inst_type=False, **params):
    """`<Address>` with one `<P type=...>` per keyword. Use `P_IP=...` style
    keys; underscores in the key become dashes in the type.

    Keywords are written in the order they are given, NOT sorted: the corpus
    writes three different `P` orders across its 162 addresses and A11 is the
    phase that must not disturb whichever one a file used, so a fixture that
    could only express one order would hide exactly that.

    ``inst_type=True`` adds `xsi:type="tP_..."`, which is what turns the
    derived types' value patterns on -- see `py61850.scl.address`. 123 of the
    corpus's 648 GSE/SMV `P` elements carry it and 39 do not, split by the
    tool that wrote the IED rather than the one that wrote the file.
    """
    inner = ""
    for key, value in params.items():
        p_type = key.replace("_", "-")
        typed = f' xsi:type="tP_{p_type}"' if inst_type else ""
        inner += f'<P type="{p_type}"{typed}>{value}</P>'
    return f"<Address>{inner}</Address>"


def gse(ld_inst, cb_name, addr="", min_time=None, max_time=None):
    times = ""
    if min_time is not None:
        times += f'<MinTime unit="s" multiplier="m">{min_time}</MinTime>'
    if max_time is not None:
        times += f'<MaxTime unit="s" multiplier="m">{max_time}</MaxTime>'
    return f'<GSE ldInst="{ld_inst}" cbName="{cb_name}">{addr}{times}</GSE>'


def smv(ld_inst, cb_name, addr=""):
    """An `<SMV>`: the link-layer address of one `SampledValueControl`.

    `addr` defaults to nothing, and that is valid: `tControlBlock` declares
    `Address` with `minOccurs="0"`, so an `SMV` naming only the control block
    it addresses is legal SCL. It is what `create_smv` writes before A17's
    generators allocate a MAC and an APPID.
    """
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


def access_point(name="S1", body="", server=True, lns="", services=""):
    """An `<AccessPoint>`. `lns` goes BESIDE the Server, not inside it --
    that is where 61850-6 puts a gateway's proxy LNs, and `services` after
    both, where `tAccessPoint`'s sequence puts it."""
    inner = f"<Server>{body}</Server>" if server else body
    return f'<AccessPoint name="{name}">{inner}{lns}{services}</AccessPoint>'


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
    """One dataset member.

    ``da_name=None`` OMITS the attribute, which is how the reference corpus
    writes a member publishing a whole data object -- 3,417 of its 6,967
    FCDAs, and 1,348 of `siemens.scd`'s 1,603. The default writes
    ``daName=""``, which this package reads as the same thing everywhere
    (:func:`py61850.scl.extref._same`) but which is a different byte sequence
    in the file; a test about Q19 wants the shape the vendors actually wrote.
    """
    attr = "" if da_name is None else f' daName="{da_name}"'
    return (f'<FCDA ldInst="{ld_inst}" prefix="{prefix}" lnClass="{ln_class}" '
            f'lnInst="{ln_inst}" doName="{do_name}"{attr} '
            f'fc="{fc}"/>')


def dataset(name, fcdas=(), desc=""):
    """A `<DataSet>`. ``desc=None`` omits the attribute.

    A dataset with no members is expressible here and is NOT valid SCL --
    `tDataSet` requires at least one `FCDA` -- which is what the tests about
    `remove_fcda` refusing to empty one are built from.
    """
    attr = "" if desc is None else f' desc="{desc}"'
    return (f'<DataSet name="{name}"{attr}>' + "".join(fcdas)
            + "</DataSet>")


def conf_data_set(max_="22", max_attributes="200", modify=None):
    """A `<ConfDataSet>`: how many datasets an IED takes, and how big.

    Every one of the reference corpus's 58 IEDs carries one, always on the
    IED-level `Services` and never on an AccessPoint's -- `max` is 22, 32, 50
    or 150 and `maxAttributes` 200, 468 or 500. Either may be passed ``None``
    to leave it off, which no corpus file does and both guards treat as
    unconstrained.
    """
    attrs = ""
    if max_ is not None:
        attrs += f' max="{max_}"'
    if max_attributes is not None:
        attrs += f' maxAttributes="{max_attributes}"'
    if modify is not None:
        attrs += f' modify="{modify}"'
    return f"<ConfDataSet{attrs}/>"


def services(*entries):
    """A `<Services>` element. It goes on an `IED` or on an `AccessPoint`;
    the reference reads the AccessPoint's first and the IED's if there is
    none, and no corpus file exercises the first half of that."""
    return "<Services>" + "".join(entries) + "</Services>"


def _dat_set(dat_set):
    """` datSet="X"`, or nothing at all when `dat_set` is None.

    **Omitting it is not a degenerate case.** 720 of the reference corpus's
    1,014 control blocks -- the majority of every report control block in it
    -- carry no `datSet`: they are unconfigured RCB templates a vendor ships
    in the ICD, and they are what a capability meets most often.
    """
    return "" if dat_set is None else f' datSet="{dat_set}"'


def gse_control(name, dat_set=None, app_id="", conf_rev="1", body=""):
    return (f'<GSEControl name="{name}"{_dat_set(dat_set)} appID="{app_id}" '
            f'confRev="{conf_rev}" type="GOOSE">{body}</GSEControl>')


def report_control(name, dat_set=None, conf_rev="1", body="", **attrs):
    """A `<ReportControl>`.

    **The default writes no `OptFields`, and `tReportControl` requires one**
    -- `minOccurs` defaults to 1 -- so the default shape is NOT valid against
    2007B4. It is kept as it is because A9's and A10's tests are built on it
    and neither asks a question `OptFields` could answer. A test that wants
    the shape a vendor actually writes passes ``body=opt_fields()``, which is
    what all 852 `ReportControl` elements in the reference corpus carry --
    720 of them with no attributes at all.
    """
    extra = "".join(f' {k}="{v}"' for k, v in sorted(attrs.items()))
    return (f'<ReportControl name="{name}"{_dat_set(dat_set)} '
            f'confRev="{conf_rev}"{extra}>{body}</ReportControl>')


def opt_fields(**attrs):
    """An `<OptFields>`: which fields travel in the report.

    Required by `tReportControl`, and valid with no attributes at all --
    every attribute in `agOptFields` carries a schema default. 720 of the
    corpus's 852 blocks write exactly ``<OptFields/>``.
    """
    return "<OptFields" + "".join(f' {k}="{v}"' for k, v in sorted(attrs.items())) + "/>"


def trg_ops(**attrs):
    """A `<TrgOps>`: what makes the report fire. `minOccurs="0"`."""
    return "<TrgOps" + "".join(f' {k}="{v}"' for k, v in sorted(attrs.items())) + "/>"


def rpt_enabled(max_="1", body=""):
    """A `<RptEnabled>`. `@max` defaults to `"1"` in the schema and is how
    many numbered instances an `indexed` block creates. `minOccurs="0"`, and
    727 of the corpus's 852 blocks leave it out."""
    attr = "" if max_ is None else f' max="{max_}"'
    return f"<RptEnabled{attr}>{body}</RptEnabled>"


def smv_opts(**attrs):
    """A `<SmvOpts>`: what travels in the sampled-value stream. Required by
    `tSampledValueControl`, exactly as `OptFields` is by `tReportControl`."""
    return "<SmvOpts" + "".join(f' {k}="{v}"' for k, v in sorted(attrs.items())) + "/>"


def conf_report_control(max_="14", max_buf=None, buf_mode=None):
    """A `<ConfReportControl>`: how many report control blocks an IED takes.

    All 58 reference-corpus IEDs declare one, always on the IED-level
    `Services` and never on an AccessPoint's -- `max` is 14, 56, 60, 64, 96
    or 200. **`maxBuf` is declared by only 26 of them**, all in `sel.scd`
    (7, 12 or 100), so ``None`` is the common shape rather than an edge.
    `bufMode` is `"both"` on 54 and absent on 2, and nothing reads it.
    """
    attrs = ""
    if max_ is not None:
        attrs += f' max="{max_}"'
    if max_buf is not None:
        attrs += f' maxBuf="{max_buf}"'
    if buf_mode is not None:
        attrs += f' bufMode="{buf_mode}"'
    return f"<ConfReportControl{attrs}/>"


def smvsc(max_="4", delivery="multicast", **attrs):
    """An `<SMVsc>`: how many sampled-value control blocks an IED takes.

    Four `mixed.scd` IEDs declare ``max="4"`` and hold exactly four blocks
    each -- the only limit in the whole corpus that is actually reached, and
    the only refusal in A12 that fires on vendor material rather than on a
    fixture. No other corpus file carries one.
    """
    extra = "".join(f' {k}="{v}"' for k, v in sorted(attrs.items()))
    delivered = "" if delivery is None else f' delivery="{delivery}"'
    limit = "" if max_ is None else f' max="{max_}"'
    return f"<SMVsc{limit}{delivered}{extra}/>"


def smv_control(name, dat_set=None, app_id="4000", conf_rev="1", body=""):
    return (f'<SampledValueControl name="{name}"{_dat_set(dat_set)} '
            f'smvID="{name}" appID="{app_id}" '
            f'confRev="{conf_rev}">{body}</SampledValueControl>')


def log_control(name, dat_set=None, conf_rev="1", ln_class="LLN0", body=""):
    """A `<LogControl>`. No corpus file carries one -- the fixtures are the
    only test material there is, which is why the capabilities that accept it
    are the ones where `datSet` alone decides the behaviour."""
    return (f'<LogControl name="{name}"{_dat_set(dat_set)} '
            f'confRev="{conf_rev}" logName="{name}" '
            f'lnClass="{ln_class}">{body}</LogControl>')


def ied_name(text, ap_ref=None):
    """An `<IEDName>`: the Edition 1 way of recording a subscriber, written as
    a CHILD of the control block. 345 of them are in the reference corpus, all
    carrying `apRef` and the subscribing IED's name as text."""
    attrs = "" if ap_ref is None else f' apRef="{ap_ref}"'
    return f"<IEDName{attrs}>{text}</IEDName>"


def setting_control(num_of_sgs="6", act_sg="1"):
    return f'<SettingControl numOfSGs="{num_of_sgs}" actSG="{act_sg}"/>'


def ext_ref(**attrs):
    return "<ExtRef" + "".join(f' {k}="{v}"' for k, v in sorted(attrs.items())) + "/>"


def inputs(*refs):
    return "<Inputs>" + "".join(refs) + "</Inputs>"


# -- the four `iedName` carriers no corpus file contains ---------------------
#
# `LNode`, `ClientLN`, `KDC` and `Association` are ZERO in all three reference
# exports, and they are four of the six elements A13's rename has to follow.
# So they are built here, from the schema rather than from a file, and the
# docstrings say which part of it -- a fixture invented out of nothing is how
# a reader acquires confident wrong answers, and saying where each shape came
# from is the cheapest guard against it.

def lnode(ied_name=None, ln_class="CSWI", ld_inst="", prefix="", ln_inst="1",
          ln_type=None):
    """An `<LNode>`: a Substation-section reference to a logical node that a
    device is expected to provide.

    `tLNode@iedName` is ``use="optional" default="None"`` and typed
    `tIEDNameOrNone`, so **omitting it and writing `None` mean the same
    thing** -- which is exactly why a removal writes the four-character string
    rather than deleting the attribute. `lnClass` is the one required
    attribute.
    """
    attrs = f' lnClass="{ln_class}"'
    if ied_name is not None:
        attrs = f' iedName="{ied_name}"' + attrs
    attrs += f' ldInst="{ld_inst}" prefix="{prefix}" lnInst="{ln_inst}"'
    if ln_type is not None:
        attrs += f' lnType="{ln_type}"'
    return f"<LNode{attrs}/>"


def client_ln(ied_name, ap_ref="S1", ld_inst="LD0", prefix="", ln_class="IHMI",
              ln_inst="1"):
    """A `<ClientLN>`: who receives a report. It goes inside `RptEnabled`,
    inside a `ReportControl`. `tClientLN` extends the `agLNRef` attribute
    group, which is where its required `iedName` comes from."""
    return (f'<ClientLN iedName="{ied_name}" apRef="{ap_ref}" '
            f'ldInst="{ld_inst}" prefix="{prefix}" lnClass="{ln_class}" '
            f'lnInst="{ln_inst}"/>')


def kdc(ied_name, ap_name="S1"):
    """A `<KDC>`: the key distribution centre an IED uses, named as another
    IED's access point. `tKDC` is two required attributes and no content, and
    it is a child of `IED` -- so one inside the IED being removed goes with
    the subtree and one in a different IED does not."""
    return f'<KDC iedName="{ied_name}" apName="{ap_name}"/>'


def association(ied_name, ap_ref="S1", ld_inst="LD0", prefix="",
                ln_class="IHMI", ln_inst="1", kind="pre-established"):
    """An `<Association>`: a two-party application association, declared under
    a `Server`. Like `ClientLN` it extends `agLNRef`, and `associationID` is
    optional where `kind` is required."""
    return (f'<Association kind="{kind}" iedName="{ied_name}" '
            f'apRef="{ap_ref}" ldInst="{ld_inst}" prefix="{prefix}" '
            f'lnClass="{ln_class}" lnInst="{ln_inst}"/>')


def rpt_enabled_with(clients, max_="1"):
    """A `<RptEnabled>` holding `ClientLN` children."""
    return f'<RptEnabled max="{max_}">' + "".join(clients) + "</RptEnabled>"


def supervision(ln_class="LGOS", inst="1", prefix="", cb_ref=None,
                dat_set=None, go_id=None):
    """An `LGOS` or `LSVS`, in the shape the reference corpus writes one.

    ``cb_ref=None`` produces the IDLE shape -- ``<Val />`` with no text --
    which is not invented: `sel.scd` carries **24** `LGOS` written exactly
    that way, for a supervision node that is allocated and not yet pointed at
    anything. That is the shape a removal blanks back to.

    `go_id` is written beside the two references and is what a rename must
    NOT touch: all 191 in the corpus are equal to a real `GSEControl@appID`.
    """
    ref_do = "SvCBRef" if ln_class == "LSVS" else "GoCBRef"
    body = doi(ref_do, dai("setSrcRef", val=cb_ref if cb_ref is not None else ""))
    body += doi("DatSet", dai("setSrcRef", val=dat_set if dat_set is not None else ""))
    if go_id is not None:
        body += doi("GoID", dai("setVal", val=go_id))
    return ln(ln_class, inst=inst, prefix=prefix, ln_type=f"T_{ln_class}",
              body=body)

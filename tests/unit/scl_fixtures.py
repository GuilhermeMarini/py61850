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
    1,015 control blocks -- the majority of every report control block in it
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

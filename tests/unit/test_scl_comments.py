# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""A document with comments in it reads exactly like the same document without.

The parser keeps comments so the file can go back to DIGSI and SEL Architect
unreformatted. The cost is that a comment becomes an ORDINARY CHILD of the
element it was written in, and its ``tag`` is not a string -- it is
``ET.Comment``, the factory that made it. Every traversal in this package
tests ``strip_ns(el.tag) == "SomeName"``, and ``"}" in tag`` on a callable
raises ``AttributeError``. So the parser change and the ``strip_ns`` guard are
one change, and this file is what holds them together.

## Why the round-trip corpus does not already cover this

It has 32 real comments in three real vendor exports, and it never builds a
model: `test_scl_roundtrip.py` parses and asks for bytes, `test_fixture_hygiene`
regex-scans the files, and nothing anywhere reads an IED, an LDevice, a
template or a control block out of a document that contains a comment. That is
the hole. It is filled here rather than by another `.scd`, because what has to
be proven is that the READER tolerates the node -- which needs a document whose
expected model is written down beside it, not a 23 MB station.

## What the fixture is built to hit

One comment at each of the five places in the package that walk an element's
children, and at every other position a comment can legally occupy in SCL:

| Position | The walk it is aimed at |
|---|---|
| Direct child of `<SCL>` | `children_local(root, "IED")`, `privates_of(root)`, `Header` |
| Inside `<Private>` | `privates_of`, and the vendor seam |
| Inside `<Communication>`, `<SubNetwork>`, `<ConnectedAP>`, `<Address>` | `Communication`'s whole build |
| Inside `<IED>`, `<AccessPoint>`, `<Server>`, `<LDevice>` | the instance skeleton |
| Inside `<LN0>` / `<LN>` | `data_sets`, `control_blocks`, `setting_control`, `ext_refs` |
| Inside `<DataSet>` and `<Inputs>` | `children_local(node.element, "FCDA" / "ExtRef")` |
| Inside `<DOI>` and `<SDI>` | `model._child_instance` |
| Inside `<DAI>`, before its `<Val>` | `model.DataAttribute._apply_instance` |
| Inside `<DataTypeTemplates>`, `<LNodeType>`, `<DOType>` | `TemplatePool._build` and the spec lookups |

:func:`_station` builds that document twice -- once with the comments, once
with them replaced by nothing -- and :func:`_facts` reduces a parsed document
to plain data. The headline test asserts the two are equal. A traversal added
later that forgets the guard changes one of those facts, and this fails.
"""

import tempfile
import unittest
from xml.etree import ElementTree as ET

from py61850.scl import (
    SclDocument,
    children_local,
    iter_local,
    privates_of,
    strip_ns,
)
from tests.unit import scl_fixtures as fx

# What every inserted comment says. One text everywhere, so a count is a count
# of positions rather than of distinct strings, and `--` is absent because XML
# forbids it inside a comment.
NOTE = " set by hand during commissioning "

# How many comments `_station` inserts. Asserted rather than derived, so that
# adding a position to the fixture without noticing fails here first.
INSERTED = 20


def _station(c):
    """The whole document, with ``c`` at every position a comment can go.

    ``c`` is either a comment or the empty string; passing ``""`` yields the
    same SCL with nothing in those positions, which is the control side of
    every comparison here.
    """
    templates = fx.templates(
        c,
        fx.lnode_type("T_CSWI", "CSWI",
                      dos=[("Pos", "DO_DPC"), ("Beh", "DO_ENS")], body=c),
        fx.do_type("DO_DPC", cdc="DPC",
                   das=[{"name": "stVal", "fc": "ST", "bType": "Dbpos"},
                        {"name": "Oper", "fc": "CO", "bType": "Struct",
                         "type": "OperDPC"}],
                   body=c),
        fx.do_type("DO_ENS", cdc="ENS",
                   das=[{"name": "stVal", "fc": "ST", "bType": "Enum",
                         "type": "BehKind"}]),
        fx.da_type("OperDPC", bdas=[{"name": "ctlVal", "bType": "BOOLEAN"}]),
        fx.enum_type("BehKind", values=[(1, "on"), (2, "blocked")]),
    )
    doi = fx.doi("Pos", body=(
        c
        + fx.dai("stVal", val="1", s_addr="db:52A", body=c)
        + fx.sdi("Oper", body=c + fx.dai("ctlVal", val="true"))))
    ln0 = fx.ln0(body=(
        c
        + fx.dataset("DS1", fcdas=[c, fx.fcda("PRO", "CSWI", "Pos", "ST",
                                              ln_inst="1")])
        + fx.gse_control("GCB1", "DS1", app_id="3001")
        + fx.setting_control()
        + fx.inputs(c, fx.ext_ref(iedName="QPC2", ldInst="PRO",
                                  lnClass="CSWI", doName="Pos", daName="stVal",
                                  srcCBName="GCB9", srcLDInst="PRO"))))
    ied = fx.ied("QPC1", type="SEL_487E", manufacturer="SEL", body=(
        c + fx.access_point("S1", lns=c, body=(
            c + fx.ldevice("PRO", body=(
                c + ln0 + fx.ln("CSWI", inst="1", ln_type="T_CSWI",
                                body=c + doi)))))))
    comms = fx.communication(c, fx.subnetwork("W01", aps=[
        c,
        fx.connected_ap("QPC1", body=(
            c
            + fx.address(IP="192.0.2.10", MAC_Address="01-0C-CD-01-00-01")
            + fx.gse("PRO", "GCB1", addr=fx.address(APPID="3001",
                                                    VLAN_ID="003"))))]))
    return fx.scl(
        c,
        fx.header(),
        c,
        fx.private("PACCT-Station", c),
        comms,
        ied,
        templates,
        version="2007", revision="B", release="4")


def _facts(d):
    """Everything the model can say about ``d``, as plain comparable data.

    Deliberately exhaustive rather than pointed: the failure mode this guards
    against is a walk that silently returns one element too many or one too
    few, and that shows up as a changed list, not as an exception.
    """
    facts = {
        "edition": d.edition,
        "release": d.release,
        "namespaces": d.namespaces,
        "header": (d.header.id, d.header.version, d.header.revision,
                   d.header.tool_id, d.header.name_structure),
        "root_privates": sorted(d.privates),
        "ied_names": d.ied_names,
        "ied_headers": {n: (h.type, h.manufacturer)
                        for n, h in d.ied_headers.items()},
        "subnetworks": [
            (sn.name, sn.type,
             [(ap.ied_name, ap.ap_name, ap.address.ip, ap.address.mac,
               sorted(ap.address.params),
               [cb.key for cb in ap.gses])
              for ap in sn.connected_aps])
            for sn in d.communication.subnetworks],
        "ip_by_ied": d.communication.ip_by_ied(),
        "cb_addresses": sorted(d.communication.control_block_addresses()),
        "lnode_type": sorted(d.templates.lnode_type("T_CSWI").objects.items()),
        "do_type": sorted(d.templates.do_type("DO_DPC").attributes),
        "enum_type": d.templates.enum_type("BehKind"),
        "da_type": sorted(d.templates.da_type("OperDPC")),
        "ieds": [],
    }
    for name in d.ied_names:
        ied = d.ied(name)
        for ld in ied.ldevices():
            for ln in ld.logical_nodes:
                facts["ieds"].append({
                    "ld": ld.ld_name,
                    "ln": ln.name,
                    "privates": sorted(ld.privates),
                    "attributes": [(a.reference(), a.mms_item(), a.fc,
                                    a.btype, a.value, a.s_addr)
                                   for a in ln.walk()],
                    "data_sets": {ds.name: [f.mms_item() for f in ds.fcdas]
                                  for ds in ln.data_sets.values()},
                    "control_blocks": sorted(cb.key + (cb.dat_set, cb.app_id)
                                             for cb in ln.control_blocks.values()),
                    "setting_control": (None if ln.setting_control is None else
                                        (ln.setting_control.num_of_sgs,
                                         ln.setting_control.act_sg)),
                    "ext_refs": [(x.ied_name, x.do_name, x.da_name,
                                  x.is_bound, x.source_key)
                                 for x in ln.ext_refs],
                })
    return facts


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name

    def doc(self, text, name="t.scd"):
        return SclDocument.parse(fx.write(self.tmpdir, name, text))


class TestTheModelIsUnaffected(_Base):
    """The headline: comments are invisible to every reader in the package."""

    def test_the_fixture_really_carries_the_comments(self):
        # Without this the comparison below could pass by inserting none.
        with_c = _station(fx.comment(NOTE))
        self.assertEqual(INSERTED, with_c.count("<!--"))
        self.assertEqual(0, _station("").count("<!--"))

    def test_the_same_document_reads_the_same_either_way(self):
        commented = _facts(self.doc(_station(fx.comment(NOTE)), "a.scd"))
        plain = _facts(self.doc(_station(""), "b.scd"))
        self.assertEqual(plain, commented)

    def test_the_model_is_not_empty(self):
        # An equality between two empty models would also pass. This states
        # what the fixture is actually worth: one IED, two logical nodes, a
        # resolved control block and a bound ExtRef.
        facts = _facts(self.doc(_station(fx.comment(NOTE))))
        self.assertEqual(("QPC1",), facts["ied_names"])
        self.assertEqual(["LLN0", "CSWI1"], [n["ln"] for n in facts["ieds"]])
        cswi = facts["ieds"][1]
        self.assertIn(
            ("QPC1PRO/CSWI1.Pos.Oper.ctlVal", "CSWI1$CO$Pos$Oper$ctlVal",
             "CO", "BOOLEAN", "true", None),
            cswi["attributes"])
        self.assertEqual([("QPC1", "PRO", "GCB1", "DS1", "3001")],
                         facts["ieds"][0]["control_blocks"])
        self.assertEqual([("QPC2", "Pos", "stVal", True,
                           ("QPC2", "PRO", "GCB9"))],
                         facts["ieds"][0]["ext_refs"])


class TestTheCommentsAreInTheTree(_Base):
    """They are kept, in the element they were written in, with their text."""

    def test_a_comment_is_a_child_of_the_element_it_was_written_in(self):
        d = self.doc(_station(fx.comment(NOTE)))
        kinds = [type(el.tag) for el in d.root]
        self.assertIn(type(ET.Comment), kinds, "no comment child on the root")

    def test_every_comment_survives_the_parse(self):
        d = self.doc(_station(fx.comment(NOTE)))
        kept = [el for el in d.root.iter() if not isinstance(el.tag, str)]
        self.assertEqual(INSERTED, len(kept))
        self.assertEqual({NOTE}, {el.text for el in kept})

    def test_they_are_written_back_out(self):
        d = self.doc(_station(fx.comment(NOTE)))
        self.assertEqual(INSERTED, d._to_bytes().count(b"<!--"))
        self.assertIn(NOTE.encode(), d._to_bytes())


class TestTheTraversalsSkipThem(_Base):
    """The five walks in the package return elements and nothing else."""

    def test_iter_local_and_children_local_return_only_elements(self):
        d = self.doc(_station(fx.comment(NOTE)))
        for el in iter_local(d.root, "LDevice"):
            self.assertIsInstance(el.tag, str)
        self.assertEqual(["QPC1"],
                         [el.get("name")
                          for el in children_local(d.root, "IED")])

    def test_privates_of_ignores_a_comment_beside_a_private(self):
        d = self.doc(_station(fx.comment(NOTE)))
        self.assertEqual(["PACCT-Station"], sorted(privates_of(d.root)))

    def test_a_comment_never_becomes_an_empty_named_child(self):
        # The guard returns "", so a search for "" must find nothing rather
        # than every comment in the document.
        d = self.doc(_station(fx.comment(NOTE)))
        self.assertEqual([], list(children_local(d.root, "")))
        self.assertEqual([], list(iter_local(d.root, "")))


class TestStripNs(unittest.TestCase):
    """The guard itself, at the two node kinds that reach it."""

    def test_an_element_tag_is_unchanged(self):
        self.assertEqual("SCL", strip_ns("{http://x}SCL"))
        self.assertEqual("SCL", strip_ns("SCL"))

    def test_a_comment_tag_yields_the_empty_string(self):
        self.assertEqual("", strip_ns(ET.Comment))

    def test_a_processing_instruction_tag_yields_the_empty_string(self):
        # Not inserted by this parser today, but the guard is what makes
        # turning `insert_pis` on later a one-line change rather than a bug.
        self.assertEqual("", strip_ns(ET.ProcessingInstruction))


class TestOutsideTheRoot(_Base):
    """A comment in the prolog or the epilog is dropped, and why.

    ``ElementTree`` has no document node: :meth:`SclDocument.parse` returns a
    root ELEMENT, and an element's children are what is inside its tags. A
    comment before ``<SCL>`` or after ``</SCL>`` is inside nothing, so the
    tree builder has nowhere to put it and it is discarded during the parse.

    **This is the one place the reference implementation does better.**
    OpenSCD parses in a browser, where ``DOMParser`` builds a real
    ``Document`` whose child list holds those comments beside the root
    element, and ``XMLSerializer`` writes them back. Closing it here would
    mean the writer carrying prolog and epilog text of its own, outside the
    tree -- no parser flag reaches it. No file in the reference corpus has
    one, which is why it is recorded here rather than fixed: these two cases
    pin the current behaviour so that a change to it, in either direction,
    is deliberate and visible.
    """

    def test_a_comment_before_the_root_is_dropped(self):
        # Inserted BETWEEN the declaration and the root, which is the only
        # place a prolog comment can go: an XML declaration must be the very
        # first thing in the entity.
        declaration, rest = fx.scl(fx.header()).split("\n", 1)
        d = self.doc(declaration + "\n" + fx.comment(NOTE) + "\n" + rest)
        self.assertEqual([], [el for el in d.root.iter()
                              if not isinstance(el.tag, str)])
        self.assertNotIn(b"<!--", d._to_bytes())

    def test_a_comment_after_the_root_is_dropped(self):
        d = self.doc(fx.scl(fx.header()) + fx.comment(NOTE) + "\n")
        self.assertNotIn(b"<!--", d._to_bytes())

    def test_a_comment_inside_the_root_is_kept(self):
        # The contrast that makes the two above a statement about POSITION
        # rather than about comments.
        d = self.doc(fx.scl(fx.comment(NOTE), fx.header()))
        self.assertIn(b"<!--", d._to_bytes())


if __name__ == "__main__":
    unittest.main()

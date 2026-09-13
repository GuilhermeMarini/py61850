# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""`py61850.scl.report_control` -- the guard, the creation and the update.

**Three corpus files are read here, not one**, which is new. A9 and A10 each
had a single working document; this phase's material is split three ways and
each file carries something the other two do not:

- `siemens.scd` has the most report material -- 414 blocks, 360 of them the
  unconfigured template shape -- and `TR1_2414`, the only IED in the corpus
  whose `RptEnabled` values would breach its own `ConfReportControl` if they
  were summed. That IED is what the element-versus-instance question turns
  on, and `test_no_corpus_block_is_genuinely_multi_instance` is the sweep
  that settles it;
- `sel.scd` has the only `maxBuf` declarations -- 26 of the corpus's 58 IEDs,
  all in that file -- and the only `ExtRef` elements subscribed to a report
  control block, which is what the rename fan-out is pinned on;
- `mixed.scd` is read by the sampled-value tests beside this one.

Everything the corpus cannot referee is on built fixtures: an IED standing at
its `max`, one standing at its `maxBuf`, a `ConfReportControl` on an
`AccessPoint`, a rename that collides.
"""

import tempfile
import unittest

from py61850.scl import (
    EditRejected,
    Insert,
    MaxReportControl,
    ReportControlInstances,
    SclDocument,
    SetAttributes,
    can_add_report_control,
    create_report_control,
    iter_local,
    max_report_control,
    number_report_control_instances,
    strip_ns,
    update_report_control,
)
from tests.unit import roundtrip
from tests.unit import scl_fixtures as fx


def _named(doc, tag, name):
    found = [el for el in iter_local(doc.root, tag) if el.get("name") == name]
    assert len(found) == 1, f"{tag} {name!r}: {len(found)} found"
    return found[0]


def station(ied_services=None, ap_services="", blocks=None, ied_name="IED1"):
    """One IED, one LDevice, one LN0 holding a dataset and some blocks."""
    if ied_services is None:
        ied_services = fx.services(fx.conf_report_control("3", max_buf="1"))
    if blocks is None:
        blocks = fx.report_control("R1", "DS", body=fx.opt_fields())
    body = fx.dataset("DS", [fx.fcda("CFG", "GGIO", "Ind1", "ST")]) + blocks
    return fx.scl(
        fx.header(),
        fx.ied(ied_name, ied_services + fx.access_point(
            "S1", fx.ldevice("CFG", fx.ln0(body=body)), services=ap_services)))


class _Base(unittest.TestCase):
    def doc(self, text=None):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        path = fx.write(self._dir.name, "a.scd", text or station())
        return SclDocument.parse(path)

    def ln0(self, doc):
        return next(iter_local(doc.root, "LN0"))

    def ied(self, doc):
        return next(iter_local(doc.root, "IED"))

    def assertRoundTrips(self, doc, edits):
        before = doc.to_bytes()
        undo = doc.apply_edit(edits)
        self.assertNotEqual(doc.to_bytes(), before)
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)


# -- maxReportControl -------------------------------------------------------

class TestMaxReportControl(_Base):
    def test_it_reads_both_limits_and_names_the_scope(self):
        doc = self.doc()
        self.assertEqual(max_report_control(doc, self.ln0(doc)),
                         MaxReportControl(3, 1, "IED"))

    def test_an_undeclared_maxbuf_is_none_rather_than_minus_one(self):
        """The reference returns `-1` for "not declared" and conflates two
        absences with it. 32 of the corpus's 58 IEDs declare a `max` and no
        `maxBuf`, so this is the common path; `None` keeps it distinct from
        an IED that declares no `ConfReportControl` at all, which returns
        `None` for the whole result. See Q29."""
        doc = self.doc(station(
            ied_services=fx.services(fx.conf_report_control("3"))))
        limit = max_report_control(doc, self.ln0(doc))
        self.assertEqual(limit.max, 3)
        self.assertIsNone(limit.max_buf)

    def test_no_declaration_at_all_is_none(self):
        doc = self.doc(station(ied_services=""))
        self.assertIsNone(max_report_control(doc, self.ln0(doc)))

    def test_the_access_point_is_read_before_the_ied(self):
        """The half of the rule no corpus file exercises: all 58
        `ConfReportControl` elements in the three exports are IED-level, and
        not one of `mixed.scd`'s 24 AccessPoint-level `Services` carries
        one."""
        doc = self.doc(station(
            ied_services=fx.services(fx.conf_report_control("3", max_buf="1")),
            ap_services=fx.services(fx.conf_report_control("9", max_buf="9"))))
        self.assertEqual(max_report_control(doc, self.ln0(doc)),
                         MaxReportControl(9, 9, "AccessPoint"))

    def test_the_ied_itself_is_an_acceptable_argument(self):
        """A12 accepts a parent A10's guards never saw, so the walk that finds
        the declaration has to include the element it was handed."""
        doc = self.doc()
        self.assertEqual(max_report_control(doc, self.ied(doc)).max, 3)

    def test_an_unparseable_limit_is_not_a_limit(self):
        doc = self.doc(station(
            ied_services=fx.services(fx.conf_report_control("lots"))))
        self.assertIsNone(max_report_control(doc, self.ln0(doc)).max)


# -- numberReportControlInstances -------------------------------------------

class TestNumberReportControlInstances(_Base):
    def test_it_splits_by_buffered(self):
        doc = self.doc(station(blocks=(
            fx.report_control("R1", body=fx.opt_fields(), buffered="true")
            + fx.report_control("R2", body=fx.opt_fields(), buffered="false")
            + fx.report_control("R3", body=fx.opt_fields()))))
        self.assertEqual(number_report_control_instances(self.ied(doc)),
                         ReportControlInstances(buffered=1, unbuffered=2))

    def test_an_absent_buffered_counts_as_unbuffered(self):
        """`tReportControl@buffered` defaults to `false`, and 8 corpus blocks
        omit it."""
        doc = self.doc(station(
            blocks=fx.report_control("R1", body=fx.opt_fields())))
        self.assertEqual(number_report_control_instances(self.ied(doc)).unbuffered, 1)

    def test_rpt_enabled_is_never_read(self):
        """The whole point of counting elements: 727 of the corpus's 852
        blocks carry no `RptEnabled`, and an instance count would need a rule
        for them. This one does not look."""
        doc = self.doc(station(blocks=fx.report_control(
            "R1", body=fx.opt_fields() + fx.rpt_enabled("7"))))
        self.assertEqual(number_report_control_instances(self.ied(doc)),
                         ReportControlInstances(0, 1))

    def test_a_foreign_namespace_element_of_the_same_name_is_not_counted(self):
        """Q27's lesson, applied to a second element: another vendor's markup
        inside a `Private` must not decide whether an edit is refused."""
        doc = self.doc(station(blocks=fx.report_control(
            "R1", body=fx.opt_fields()) + fx.private(
                "Vendor", '<ReportControl xmlns="urn:vendor" name="X"/>')))
        self.assertEqual(number_report_control_instances(self.ied(doc)).unbuffered, 1)

    def test_the_whole_document_is_an_acceptable_scope(self):
        doc = self.doc()
        self.assertEqual(number_report_control_instances(doc.root).total, 1)


# -- canAddReportControl ----------------------------------------------------

class TestCanAddReportControl(_Base):
    def test_an_ied_that_declares_no_limit_is_unconstrained(self):
        doc = self.doc(station(ied_services=""))
        self.assertTrue(can_add_report_control(doc, self.ln0(doc)))

    def test_below_the_maximum_it_is_allowed(self):
        doc = self.doc()
        self.assertTrue(can_add_report_control(doc, self.ln0(doc)))

    def test_at_the_maximum_it_is_refused(self):
        doc = self.doc(station(
            ied_services=fx.services(fx.conf_report_control("2")),
            blocks=fx.report_control("R1", body=fx.opt_fields())
            + fx.report_control("R2", body=fx.opt_fields())))
        self.assertFalse(can_add_report_control(doc, self.ln0(doc)))

    def test_maxbuf_refuses_a_buffered_block_the_total_would_allow(self):
        """`max` and `maxBuf` are separate gates: room in the total does not
        mean room among the buffered."""
        doc = self.doc(station(
            ied_services=fx.services(fx.conf_report_control("9", max_buf="1")),
            blocks=fx.report_control("R1", body=fx.opt_fields(), buffered="true")))
        self.assertTrue(can_add_report_control(doc, self.ln0(doc)))
        self.assertFalse(can_add_report_control(doc, self.ln0(doc), buffered=True))

    def test_maxbuf_is_not_consulted_for_an_unbuffered_block(self):
        doc = self.doc(station(
            ied_services=fx.services(fx.conf_report_control("9", max_buf="0")),
            blocks=fx.report_control("R1", body=fx.opt_fields())))
        self.assertTrue(can_add_report_control(doc, self.ln0(doc)))

    def test_several_at_once_are_judged_together(self):
        doc = self.doc(station(
            ied_services=fx.services(fx.conf_report_control("3")),
            blocks=fx.report_control("R1", body=fx.opt_fields())))
        self.assertTrue(can_add_report_control(doc, self.ln0(doc), new_instances=2))
        self.assertFalse(can_add_report_control(doc, self.ln0(doc), new_instances=3))

    def test_the_limit_is_counted_in_the_scope_that_declared_it(self):
        """Q26's rule, met again. The AccessPoint's declaration counts the
        blocks under the AccessPoint, not those under the IED."""
        doc = self.doc(station(
            ied_services=fx.services(fx.conf_report_control("99")),
            ap_services=fx.services(fx.conf_report_control("1")),
            blocks=fx.report_control("R1", body=fx.opt_fields())))
        self.assertFalse(can_add_report_control(doc, self.ln0(doc)))

    def test_buf_mode_is_not_read(self):
        """Deliberately absent, for the reason Q26 recorded about
        `ConfDataSet@modify`: the reference's guard names `max` and `maxBuf`
        and not this, and no corpus file would be decided by it."""
        doc = self.doc(station(ied_services=fx.services(
            fx.conf_report_control("9", buf_mode="unbuffered"))))
        self.assertTrue(can_add_report_control(doc, self.ln0(doc), buffered=True))

    def test_something_that_is_not_an_element_is_refused(self):
        doc = self.doc()
        self.assertFalse(can_add_report_control(doc, "LN0"))


# -- createReportControl ----------------------------------------------------

class TestCreateReportControl(_Base):
    def test_it_returns_a_list_of_one_insert(self):
        doc = self.doc()
        edits = create_report_control(doc, self.ln0(doc), "R2")
        self.assertEqual(len(edits), 1)
        self.assertIsInstance(edits[0], Insert)

    def test_the_required_opt_fields_is_always_written(self):
        """`tReportControl` declares `OptFields` with `minOccurs` defaulting
        to 1, so a block without one is invalid -- and 720 corpus blocks
        carry an EMPTY one, which is valid because every `agOptFields`
        attribute has a default."""
        doc = self.doc()
        doc.apply_edit(create_report_control(doc, self.ln0(doc), "R2"))
        children = [strip_ns(c.tag) for c in _named(doc, "ReportControl", "R2")]
        self.assertEqual(children, ["OptFields"])

    def test_trg_ops_and_rpt_enabled_are_written_only_when_asked_for(self):
        doc = self.doc()
        doc.apply_edit(create_report_control(
            doc, self.ln0(doc), "R2", trg_ops={"dchg": "true"},
            opt_fields={"seqNum": "true"}, instances="4"))
        block = _named(doc, "ReportControl", "R2")
        self.assertEqual([strip_ns(c.tag) for c in block],
                         ["TrgOps", "OptFields", "RptEnabled"])
        self.assertEqual(
            [c.get("max") for c in block if strip_ns(c.tag) == "RptEnabled"],
            ["4"])
        self.assertEqual(
            [c.get("seqNum") for c in block if strip_ns(c.tag) == "OptFields"],
            ["true"])

    def test_the_children_are_written_in_schema_order(self):
        """`tReportControl`'s sequence is TrgOps, OptFields, RptEnabled, and a
        child in the wrong position loads here and fails in DIGSI."""
        doc = self.doc()
        doc.apply_edit(create_report_control(
            doc, self.ln0(doc), "R2", trg_ops={"period": "true"}, instances="2"))
        self.assertEqual(
            [strip_ns(c.tag) for c in _named(doc, "ReportControl", "R2")],
            ["TrgOps", "OptFields", "RptEnabled"])

    def test_indexed_false_resets_the_instance_count_to_one(self):
        """The reference's own rule, and the reason `TR1_2414`'s four blocks
        declare 22 instances and have four."""
        doc = self.doc()
        doc.apply_edit(create_report_control(
            doc, self.ln0(doc), "R2", indexed="false", instances="7"))
        enabled = next(iter_local(_named(doc, "ReportControl", "R2"),
                                  "RptEnabled"))
        self.assertEqual(enabled.get("max"), "1")

    def test_indexed_is_never_written_as_true(self):
        """Not one of the corpus's 852 blocks writes it, and it is the schema
        default; writing it in would change bytes nobody asked about."""
        doc = self.doc()
        doc.apply_edit(create_report_control(doc, self.ln0(doc), "R2"))
        self.assertIsNone(_named(doc, "ReportControl", "R2").get("indexed"))

    def test_conf_rev_starts_at_one(self):
        doc = self.doc()
        doc.apply_edit(create_report_control(doc, self.ln0(doc), "R2"))
        self.assertEqual(_named(doc, "ReportControl", "R2").get("confRev"), "1")

    def test_an_ied_resolves_to_its_first_ln0(self):
        """The reference's *"in the later case first `LN0` is picked"*, which
        is a wider input than anything A10 accepted."""
        doc = self.doc()
        doc.apply_edit(create_report_control(doc, self.ied(doc), "R2"))
        self.assertIs(doc.parent_of(_named(doc, "ReportControl", "R2")),
                      self.ln0(doc))

    def test_an_ln_is_a_direct_parent_too(self):
        """`tLN` declares `ReportControl` as well as `tLN0` does, and the
        reference's parameter name covers it."""
        doc = self.doc(station(blocks="") .replace(
            "</LN0>", "</LN0>" + fx.ln("MMXU", body="")))
        node = next(iter_local(doc.root, "LN"))
        doc.apply_edit(create_report_control(doc, node, "R9"))
        self.assertIs(doc.parent_of(_named(doc, "ReportControl", "R9")), node)

    def test_a_missing_name_is_refused(self):
        """A10's line, held: allocating one is A17's. See Q27."""
        doc = self.doc()
        with self.assertRaisesRegex(EditRejected, "A17"):
            create_report_control(doc, self.ln0(doc), "")

    def test_an_empty_conf_rev_is_refused(self):
        doc = self.doc()
        with self.assertRaisesRegex(EditRejected, "confRev"):
            create_report_control(doc, self.ln0(doc), "R2", conf_rev="")

    def test_a_name_already_used_is_refused(self):
        doc = self.doc()
        with self.assertRaisesRegex(EditRejected, "already"):
            create_report_control(doc, self.ln0(doc), "R1")

    def test_a_name_used_by_a_different_kind_of_block_is_refused(self):
        """`control_block_obj_ref` carries no element type, so two blocks of
        different kinds sharing a name in one LN produce the same 61850-7-2
        reference. No corpus logical node holds such a pair. See Q29."""
        doc = self.doc(station(blocks=fx.gse_control("GCB1", "DS")))
        with self.assertRaisesRegex(EditRejected, "GSEControl"):
            create_report_control(doc, self.ln0(doc), "GCB1")

    def test_a_dat_set_naming_nothing_is_refused(self):
        doc = self.doc()
        with self.assertRaisesRegex(EditRejected, "keyref"):
            create_report_control(doc, self.ln0(doc), "R2", dat_set="NOPE")

    def test_force_skips_the_dat_set_and_the_guard(self):
        doc = self.doc(station(
            ied_services=fx.services(fx.conf_report_control("1"))))
        with self.assertRaises(EditRejected):
            create_report_control(doc, self.ln0(doc), "R2")
        self.assertEqual(len(create_report_control(
            doc, self.ln0(doc), "R2", dat_set="NOPE", force=True)), 1)

    def test_the_refusal_names_the_declaration(self):
        doc = self.doc(station(
            ied_services=fx.services(fx.conf_report_control("1"))))
        with self.assertRaisesRegex(EditRejected, "max=1"):
            create_report_control(doc, self.ln0(doc), "R2")

    def test_a_parent_that_cannot_hold_one_is_refused(self):
        doc = self.doc()
        with self.assertRaisesRegex(EditRejected, "may not hold"):
            create_report_control(doc, next(iter_local(doc.root, "DataSet")), "R2")

    def test_it_is_one_history_entry(self):
        doc = self.doc()
        self.assertRoundTrips(doc, create_report_control(doc, self.ln0(doc), "R2"))

    def test_the_new_element_carries_the_document_s_namespace(self):
        doc = self.doc()
        doc.apply_edit(create_report_control(doc, self.ln0(doc), "R2"))
        self.assertEqual(_named(doc, "ReportControl", "R2").tag,
                         self.ln0(doc).tag.replace("LN0", "ReportControl"))

    def test_a_document_without_a_namespace_gets_an_element_without_one(self):
        doc = self.doc(station().replace(
            ' xmlns="http://www.iec.ch/61850/2003/SCL"', ""))
        doc.apply_edit(create_report_control(doc, self.ln0(doc), "R2"))
        self.assertEqual(_named(doc, "ReportControl", "R2").tag, "ReportControl")


# -- updateReportControl ----------------------------------------------------

class TestUpdateReportControl(_Base):
    def test_the_input_edit_comes_back_first(self):
        """Q23's rule: everything the caller should apply, input edit first,
        so it is one `apply_edit` call and one history entry."""
        doc = self.doc()
        edit = SetAttributes(_named(doc, "ReportControl", "R1"),
                             {"bufTime": "250"})
        edits = update_report_control(doc, edit)
        self.assertIs(edits[0], edit)

    def test_an_ordinary_attribute_adds_nothing(self):
        doc = self.doc()
        edit = SetAttributes(_named(doc, "ReportControl", "R1"),
                             {"desc": "feeder"})
        self.assertEqual(update_report_control(doc, edit), [edit])

    def test_a_dat_set_change_is_delegated_to_a9(self):
        """`update_dat_set` owns what setting `datSet` means -- the exclusive
        dataset renamed with it, and `confRev` left alone for a rename (Q25).
        Calling it rather than repeating it is why the two paths agree."""
        doc = self.doc()
        edit = SetAttributes(_named(doc, "ReportControl", "R1"),
                             {"datSet": "RENAMED"})
        edits = update_report_control(doc, edit)
        self.assertEqual(
            [(strip_ns(e.element.tag), e.attributes) for e in edits[1:]],
            [("DataSet", {"name": "RENAMED"})])

    def test_a_rename_repoints_every_subscriber(self):
        """The fan-out the reference documents for neither update function.
        `sel.scd` holds 58 ExtRefs bound to a report control block. See Q29."""
        doc = self.doc(_station_with_subscriber())
        edit = SetAttributes(_named(doc, "ReportControl", "R1"), {"name": "R9"})
        edits = update_report_control(doc, edit)
        self.assertEqual([(strip_ns(e.element.tag), e.attributes)
                          for e in edits[1:]],
                         [("ExtRef", {"srcCBName": "R9"})])

    def test_a_rename_to_the_same_name_changes_nothing(self):
        doc = self.doc(_station_with_subscriber())
        edit = SetAttributes(_named(doc, "ReportControl", "R1"), {"name": "R1"})
        self.assertEqual(update_report_control(doc, edit), [edit])

    def test_clearing_the_name_is_refused(self):
        doc = self.doc()
        edit = SetAttributes(_named(doc, "ReportControl", "R1"), {"name": ""})
        with self.assertRaisesRegex(EditRejected, "required"):
            update_report_control(doc, edit)

    def test_a_rename_onto_another_block_is_refused(self):
        doc = self.doc(station(
            blocks=fx.report_control("R1", "DS", body=fx.opt_fields())
            + fx.report_control("R2", body=fx.opt_fields())))
        edit = SetAttributes(_named(doc, "ReportControl", "R1"), {"name": "R2"})
        with self.assertRaisesRegex(EditRejected, "collide"):
            update_report_control(doc, edit)

    def test_indexed_false_resets_rpt_enabled(self):
        doc = self.doc(station(blocks=fx.report_control(
            "R1", "DS", body=fx.opt_fields() + fx.rpt_enabled("7"))))
        edit = SetAttributes(_named(doc, "ReportControl", "R1"),
                             {"indexed": "false"})
        edits = update_report_control(doc, edit)
        self.assertEqual([(strip_ns(e.element.tag), e.attributes)
                          for e in edits[1:]],
                         [("RptEnabled", {"max": "1"})])

    def test_indexed_false_on_a_block_with_no_rpt_enabled_adds_nothing(self):
        """The majority shape: 727 corpus blocks carry no `RptEnabled`, and
        the schema default is already 1."""
        doc = self.doc()
        edit = SetAttributes(_named(doc, "ReportControl", "R1"),
                             {"indexed": "false"})
        self.assertEqual(update_report_control(doc, edit), [edit])

    def test_supervision_cannot_be_turned_on(self):
        """A14's, and refused rather than quietly ignored -- the same answer
        the six functions that already carry the flag give."""
        doc = self.doc()
        edit = SetAttributes(_named(doc, "ReportControl", "R1"), {"desc": "x"})
        with self.assertRaisesRegex(EditRejected, "not written yet"):
            update_report_control(doc, edit, ignore_supervision=False)

    def test_the_wrong_kind_of_edit_is_refused(self):
        doc = self.doc()
        with self.assertRaisesRegex(EditRejected, "SetAttributes"):
            update_report_control(doc, "not an edit")

    def test_another_kind_of_control_block_is_refused(self):
        doc = self.doc(station(blocks=fx.gse_control("GCB1", "DS")))
        edit = SetAttributes(_named(doc, "GSEControl", "GCB1"), {"desc": "x"})
        with self.assertRaisesRegex(EditRejected, "not a ReportControl"):
            update_report_control(doc, edit)

    def test_it_is_one_history_entry(self):
        doc = self.doc(_station_with_subscriber())
        self.assertRoundTrips(doc, update_report_control(
            doc, SetAttributes(_named(doc, "ReportControl", "R1"),
                               {"name": "R9"})))


def _station_with_subscriber():
    """Two IEDs: one publishing a report, one subscribed to it by `src*`."""
    publisher = fx.ied("PUB", fx.services(fx.conf_report_control("9"))
                       + fx.access_point("S1", fx.ldevice("CFG", fx.ln0(body=(
                           fx.dataset("DS", [fx.fcda("CFG", "GGIO", "Ind1", "ST")])
                           + fx.report_control("R1", "DS", body=fx.opt_fields()))))))
    subscriber = fx.ied("SUB", fx.access_point("S1", fx.ldevice("CFG", fx.ln0(
        body=fx.inputs(fx.ext_ref(
            iedName="PUB", ldInst="CFG", lnClass="GGIO", doName="Ind1",
            daName="stVal", serviceType="Report", srcCBName="R1",
            srcLDInst="CFG", srcLNClass="LLN0"))))))
    return fx.scl(fx.header(), publisher, subscriber)


# -- the reference corpus ---------------------------------------------------

class TestCorpus(unittest.TestCase):
    """Vendor exports, where a fixture can only agree with the code that made
    it."""

    def corpus(self, name):
        path = roundtrip.CORPUS / name
        if not path.is_file():
            self.skipTest("the corpus is not in this distribution")
        return SclDocument.parse(path)

    def test_no_corpus_block_is_genuinely_multi_instance(self):
        """**The measurement the element-versus-instance question turns on.**

        `siemens.scd`'s `TR1_2414` declares `max="14"` and holds four blocks
        whose `RptEnabled@max` values sum to 22, which reads as a vendor
        breaching its own declaration. It is not one: all four carry
        `indexed="false"`, which the reference documents as resetting the
        instance count to 1.

        Swept over all three exports, **not one `ReportControl` has `indexed`
        absent-or-true together with an `RptEnabled@max` above 1**. So
        elements and instances are equal for every IED in the corpus and no
        file refutes either reading -- which is what leaves the reference's
        documented behaviour to decide it. See Q28.
        """
        multi = []
        for name in ("sel.scd", "mixed.scd", "siemens.scd"):
            doc = self.corpus(name)
            for block in iter_local(doc.root, "ReportControl"):
                if block.get("indexed") == "false":
                    continue
                for child in block:
                    if strip_ns(child.tag) != "RptEnabled":
                        continue
                    if (child.get("max") or "1") != "1":
                        multi.append((name, block.get("name")))
        self.assertEqual(multi, [])

    def test_a_vendor_element_also_called_ied_is_not_one(self):
        """Q27 found 127 elements named `DataSet` that are not datasets;
        **the same is true of `IED`**, one level up. `siemens.scd` holds 14
        `IED` elements in `http://www.siemens.com/energy/2011/11/Siedig`
        beside its 14 real ones -- one per IED -- so selecting an IED by
        local name picks a vendor's bookkeeping half the time. It is a fresh
        measurement rather than a restatement: A10 checked the leaves and
        this is the root of a whole IED section.

        Nothing here is fooled by it, because the declaration is looked up in
        the document's OWN namespace and the Siedig element holds no
        `Services`; the vendor element simply answers "unconstrained".
        """
        doc = self.corpus("siemens.scd")
        namespace = doc.root.tag[:doc.root.tag.index("}") + 1]
        real = [e for e in iter_local(doc.root, "IED")
                if e.tag.startswith(namespace)]
        foreign = [e for e in iter_local(doc.root, "IED")
                   if not e.tag.startswith(namespace)]
        self.assertEqual((len(real), len(foreign)), (14, 14))
        self.assertIsNone(max_report_control(doc, foreign[0]))

    def test_the_ied_that_looks_like_a_breach_fits_as_elements(self):
        doc = self.corpus("siemens.scd")
        namespace = doc.root.tag[:doc.root.tag.index("}") + 1]
        ied = next(e for e in iter_local(doc.root, "IED")
                   if e.get("name") == "TR1_2414" and e.tag.startswith(namespace))
        self.assertEqual(max_report_control(doc, ied),
                         MaxReportControl(14, None, "IED"))
        self.assertEqual(number_report_control_instances(ied),
                         ReportControlInstances(buffered=1, unbuffered=3))
        self.assertTrue(can_add_report_control(doc, ied))

    def test_every_corpus_ied_is_below_its_own_declared_maximum(self):
        """58 IEDs, three vendor tools, and the guard agrees with all of them
        -- which is what says the limits are real material and only the
        breach is synthetic. `sel.scd` is read for `maxBuf`: 26 of the
        corpus's declarations carry one and every one of them is there."""
        counted, with_max_buf = 0, 0
        for name in ("sel.scd", "mixed.scd", "siemens.scd"):
            doc = self.corpus(name)
            namespace = doc.root.tag[:doc.root.tag.index("}") + 1]
            for ied in iter_local(doc.root, "IED"):
                if not ied.tag.startswith(namespace):
                    continue
                limit = max_report_control(doc, ied)
                if limit is None:
                    continue
                counted += 1
                with_max_buf += limit.max_buf is not None
                self.assertTrue(can_add_report_control(doc, ied), ied.get("name"))
                self.assertTrue(
                    can_add_report_control(doc, ied, buffered=True),
                    ied.get("name"))
        self.assertEqual((counted, with_max_buf), (58, 26))

    def test_every_report_control_in_the_corpus_carries_an_opt_fields(self):
        """What `create_report_control` always writing one is built on: the
        schema requires it, and all 852 corpus blocks have one -- 720 of them
        with no attributes at all."""
        total, empty = 0, 0
        for name in ("sel.scd", "mixed.scd", "siemens.scd"):
            doc = self.corpus(name)
            for block in iter_local(doc.root, "ReportControl"):
                total += 1
                fields = [c for c in block if strip_ns(c.tag) == "OptFields"]
                self.assertEqual(len(fields), 1, block.get("name"))
                empty += not fields[0].attrib
        self.assertEqual((total, empty), (852, 720))

    def test_not_one_corpus_block_writes_indexed_true(self):
        written = set()
        for name in ("sel.scd", "mixed.scd", "siemens.scd"):
            for block in iter_local(self.corpus(name).root, "ReportControl"):
                written.add(block.get("indexed"))
        self.assertEqual(written, {None, "false"})

    def test_a_rename_on_the_corpus_repoints_its_real_subscribers(self):
        """`sel.scd` is the only file with an `ExtRef` bound to a report
        control block: 58 of them over 29 blocks, all `serviceType="Report"`.
        Renaming one has to follow them."""
        doc = self.corpus("sel.scd")
        followed = 0
        for block in iter_local(doc.root, "ReportControl"):
            edits = update_report_control(
                doc, SetAttributes(block, {"name": block.get("name") + "_X"}))
            followed += sum(1 for e in edits[1:]
                            if strip_ns(e.element.tag) == "ExtRef")
        self.assertEqual(followed, 58)


if __name__ == "__main__":
    unittest.main()

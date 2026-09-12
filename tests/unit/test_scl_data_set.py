# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""`py61850.scl.data_set` -- the guards, the creation, and the two removals.

The built fixtures carry what the corpus does not: an IED standing at its own
`ConfDataSet` limit, a `ConfDataSet` on an `AccessPoint`, a dataset published
by two control blocks, a removal that would empty one. Everything the corpus
DOES carry is asserted against the corpus at the bottom of the file, for the
reason `test_scl_control_block` gives: a fixture agreeing with the code that
built it proves less than a vendor export agreeing with it.

**Q19 is this file's subject as much as any capability is.** The whole-object
member -- an `FCDA` with a `doName` and no `daName` -- is half of every
dataset in the corpus, and whether removing one breaks an `ExtRef` bound to
one of its attributes is what `fcda_covers_ext_ref` answers. The four
subscriptions in `sel.scd` that have exactly that shape are swept at the
bottom; the behaviour is pinned on fixtures built to look like them.
"""

import tempfile
import unittest
from xml.etree import ElementTree as ET

from py61850.scl import (
    CONF_REV_STEP,
    EditRejected,
    Insert,
    MaxAttributes,
    Remove,
    SclDocument,
    SetAttributes,
    can_add_data_set,
    can_add_fcda,
    create_data_set,
    control_blocks,
    fcda_covers_ext_ref,
    fcda_subscriptions,
    is_subscribed,
    iter_local,
    max_attributes,
    remove_control_block,
    remove_data_set,
    remove_fcda,
    strip_ns,
    update_data_set,
    updated_conf_rev_edits,
)
from py61850.scl.data_set import _subscriptions_to
from tests.unit import roundtrip
from tests.unit import scl_fixtures as fx


def _named(doc, tag, name):
    found = [el for el in iter_local(doc.root, tag) if el.get("name") == name]
    assert len(found) == 1, f"{tag} {name!r}: {len(found)} found"
    return found[0]


def _members(data_set):
    return [f for f in data_set if strip_ns(f.tag) == "FCDA"]


#: DS1's members. The third is the shape Q19 is about: a data object and no
#: attribute, which publishes every attribute of it. `da_name=None` omits the
#: attribute entirely, as the 3,417 whole-object members of the reference
#: corpus do.
DS1_MEMBERS = (
    fx.fcda("CFG", "GGIO", "Ind01", "ST", da_name="stVal"),
    fx.fcda("CFG", "GGIO", "Ind02", "ST", da_name="stVal"),
    fx.fcda("CFG", "GGIO", "Ind03", "ST", da_name=None),
)

DS2_MEMBERS = (fx.fcda("CFG", "GGIO", "Ind01", "ST", da_name="stVal"),)


def _inputs():
    """Three subscriptions, each there to be watched for a different reason.

    `in1` takes a member literally. `in2` takes one ATTRIBUTE of the
    whole-object member `Ind03`, which is Q19 and which no literal comparison
    finds. `in3` takes `Ind01.stVal` -- a member of BOTH datasets -- but names
    `RCB1`, which publishes DS2, so nothing done to DS1 may touch it.
    """
    return fx.inputs(
        fx.ext_ref(intAddr="in1", iedName="PUB", ldInst="CFG", lnClass="GGIO",
                   doName="Ind01", daName="stVal", serviceType="GOOSE",
                   srcCBName="GCB1", srcLDInst="CFG", srcLNClass="LLN0"),
        fx.ext_ref(iedName="PUB", ldInst="CFG", lnClass="GGIO",
                   doName="Ind03", daName="stVal", serviceType="GOOSE",
                   srcCBName="GCB1", srcLDInst="CFG", srcLNClass="LLN0"),
        fx.ext_ref(intAddr="in3", iedName="PUB", ldInst="CFG", lnClass="GGIO",
                   doName="Ind01", daName="stVal", serviceType="Report",
                   srcCBName="RCB1", srcLDInst="CFG", srcLNClass="LLN0"),
    )


def station(ln0_body=None, ied_services=None, ap_services="", ld_inst="CFG"):
    """`PUB` publishes two datasets; `SUB` takes three inputs from them.

    DS1 is published by TWO control blocks, which is the fan-out every
    `confRev` and re-pointing assertion here turns on and which no file in the
    reference corpus carries -- all 295 of its datasets have at most one
    publisher.
    """
    if ln0_body is None:
        ln0_body = (fx.dataset("DS1", DS1_MEMBERS)
                    + fx.dataset("DS2", DS2_MEMBERS)
                    + fx.report_control("RCB1", "DS2")
                    + fx.gse_control("GCB1", "DS1")
                    + fx.smv_control("MSVCB1", "DS1"))
    if ied_services is None:
        ied_services = fx.services(fx.conf_data_set(max_="5",
                                                    max_attributes="4"))
    publisher = fx.ied("PUB", ied_services + fx.access_point(
        "S1", fx.ldevice(ld_inst, fx.ln0(body=ln0_body)),
        services=ap_services))
    subscriber = fx.ied("SUB", fx.access_point(
        "S1", fx.ldevice("PROT", fx.ln0(body=_inputs()))))
    return fx.scl(fx.header(), publisher, subscriber)


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._written = 0

    def doc(self, text=None):
        self._written += 1
        return SclDocument.parse(fx.write(
            self._tmp.name, f"station{self._written}.scd",
            text if text is not None else station()))

    def ln0(self, doc):
        return next(iter_local(doc.root, "LN0"))

    def assertRoundTrips(self, doc, edits):
        """Apply, check something changed, undo, and get the bytes back.

        The byte comparison rather than a tree comparison, because that is the
        guarantee this library carries and because Q13 and Q15 were both found
        by it and not by comparing values.
        """
        before = doc.to_bytes()
        undo = doc.apply_edit(edits)
        self.assertNotEqual(doc.to_bytes(), before)
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)


# -- canAddDataSet ----------------------------------------------------------

class TestCanAddDataSet(_Base):
    def test_an_ied_that_declares_no_limit_is_unconstrained(self):
        """A8's rule applied again: a restriction that cannot be resolved is
        not a restriction. `Services` is optional in the schema and a
        hand-written ICD carrying none would otherwise be able to hold no
        dataset at all."""
        doc = self.doc(station(ied_services=""))
        self.assertTrue(can_add_data_set(doc, self.ln0(doc)))

    def test_below_the_declared_maximum_it_is_allowed(self):
        doc = self.doc()
        self.assertTrue(can_add_data_set(doc, self.ln0(doc)))

    def test_at_the_declared_maximum_it_is_refused(self):
        """No IED in the reference corpus is near its own limit -- at most 22
        datasets against a `max` of 50 -- so this branch is built rather than
        found."""
        doc = self.doc(station(ied_services=fx.services(
            fx.conf_data_set(max_="2"))))
        self.assertFalse(can_add_data_set(doc, self.ln0(doc)))

    def test_the_count_is_taken_in_the_scope_that_declared_the_limit(self):
        """The IED declared it, so every dataset in the IED counts -- not
        only those on the logical node being added to. A second logical device
        pushes an LN0 holding one dataset over a limit of two."""
        ln0_body = fx.dataset("DS1", DS1_MEMBERS)
        second = fx.ldevice("OTHER", fx.ln0(body=(
            fx.dataset("DSA", DS2_MEMBERS) + fx.dataset("DSB", DS2_MEMBERS))))
        publisher = fx.ied("PUB", fx.services(fx.conf_data_set(max_="3"))
                           + fx.access_point("S1", fx.ldevice(
                               "CFG", fx.ln0(body=ln0_body)) + second))
        doc = self.doc(fx.scl(fx.header(), publisher))
        self.assertEqual(len(list(iter_local(doc.root, "DataSet"))), 3)
        self.assertFalse(can_add_data_set(doc, self.ln0(doc)))

    def test_an_access_point_declaration_wins_over_the_ied_s(self):
        """The reference reads the `AccessPoint` first and the `IED` only if
        that has none. **No corpus file exercises it**: all 58 IEDs declare a
        `ConfDataSet` and every one is on the IED-level `Services`, with not
        one of `mixed.scd`'s 24 AccessPoint-level `Services` carrying one. So
        this is the reference's documented behaviour, pinned on a fixture."""
        doc = self.doc(station(
            ied_services=fx.services(fx.conf_data_set(max_="99")),
            ap_services=fx.services(fx.conf_data_set(max_="2"))))
        self.assertFalse(can_add_data_set(doc, self.ln0(doc)))

    def test_an_element_that_may_not_hold_a_dataset_answers_no(self):
        doc = self.doc()
        ldevice = next(iter_local(doc.root, "LDevice"))
        self.assertFalse(can_add_data_set(doc, ldevice))
        self.assertFalse(can_add_data_set(doc, "LN0"))

    def test_an_ln_may_hold_one_too(self):
        """`DataSet` is a child of `tLN` as well as `tLN0`, which the
        reference's parameter name does not say."""
        doc = self.doc(fx.scl(fx.header(), fx.ied("PUB", fx.access_point(
            "S1", fx.ldevice("CFG", fx.ln0() + fx.ln("GGIO", "1"))))))
        ln = next(iter_local(doc.root, "LN"))
        self.assertTrue(can_add_data_set(doc, ln))

    def test_a_limit_that_does_not_parse_is_not_a_limit(self):
        doc = self.doc(station(ied_services=fx.services(
            fx.conf_data_set(max_="unbounded"))))
        self.assertTrue(can_add_data_set(doc, self.ln0(doc)))

    def test_a_vendor_element_also_called_dataset_is_not_counted(self):
        """`mixed.scd` holds 113 elements named `DataSet` in a Siemens
        namespace, inside a `Private`, and `siemens.scd` 14 more. Counting by
        local name would have this guard refuse an IED because of another
        vendor's tooling."""
        private = ('<Private type="Siedig">'
                   '<GooseApplication xmlns="http://example.invalid/vendor">'
                   '<DataSet/><DataSet/><DataSet/>'
                   "</GooseApplication></Private>")
        doc = self.doc(station(ln0_body=(
            private + fx.dataset("DS1", DS1_MEMBERS)),
            ied_services=fx.services(fx.conf_data_set(max_="2"))))
        self.assertEqual(len(list(iter_local(doc.root, "DataSet"))), 4)
        self.assertTrue(can_add_data_set(doc, self.ln0(doc)))


# -- maxAttributes and canAddFCDA -------------------------------------------

class TestMaxAttributes(_Base):
    def test_a_member_and_its_dataset_give_the_same_answer(self):
        doc = self.doc()
        data_set = _named(doc, "DataSet", "DS1")
        self.assertEqual(max_attributes(doc, data_set),
                         MaxAttributes(4, "IED"))
        self.assertEqual(max_attributes(doc, _members(data_set)[0]),
                         MaxAttributes(4, "IED"))

    def test_the_scope_says_where_the_declaration_was_found(self):
        doc = self.doc(station(ap_services=fx.services(
            fx.conf_data_set(max_attributes="9"))))
        self.assertEqual(max_attributes(doc, _named(doc, "DataSet", "DS1")),
                         MaxAttributes(9, "AccessPoint"))

    def test_nothing_declared_is_no_limit_rather_than_zero(self):
        doc = self.doc(station(ied_services=""))
        self.assertIsNone(max_attributes(doc, _named(doc, "DataSet", "DS1")))

    def test_a_conf_data_set_without_max_attributes_declares_no_limit(self):
        doc = self.doc(station(ied_services=fx.services(
            fx.conf_data_set(max_attributes=None))))
        self.assertIsNone(max_attributes(doc, _named(doc, "DataSet", "DS1")))

    def test_something_that_is_not_an_element_answers_nothing(self):
        doc = self.doc()
        self.assertIsNone(max_attributes(doc, "DS1"))


class TestCanAddFcda(_Base):
    def test_below_the_limit_it_is_allowed(self):
        doc = self.doc()
        self.assertTrue(can_add_fcda(doc, _named(doc, "DataSet", "DS1")))

    def test_at_the_limit_it_is_refused(self):
        """`maxAttributes` counts ONE dataset's members, and the corpus is
        what proves it: twelve of `mixed.scd`'s fourteen IEDs declare 200
        while holding 242 to 477 members across their datasets, and no single
        dataset exceeds 122. Read as a per-IED total, a vendor's export would
        violate its own declaration twelve times over."""
        doc = self.doc(station(ied_services=fx.services(
            fx.conf_data_set(max_attributes="3"))))
        self.assertFalse(can_add_fcda(doc, _named(doc, "DataSet", "DS1")))
        self.assertTrue(can_add_fcda(doc, _named(doc, "DataSet", "DS2")))

    def test_no_declaration_is_no_limit(self):
        doc = self.doc(station(ied_services=""))
        self.assertTrue(can_add_fcda(doc, _named(doc, "DataSet", "DS1")))

    def test_anything_that_is_not_a_dataset_answers_no(self):
        doc = self.doc()
        self.assertFalse(can_add_fcda(doc, self.ln0(doc)))


# -- createDataSet ----------------------------------------------------------

class TestCreateDataSet(_Base):
    def test_it_returns_one_insert_in_a_list(self):
        """A list, where the reference returns a bare `Insert`: one rule for
        applying what comes back, which is Q23's, and the shape `subscribe`
        already returns."""
        doc = self.doc()
        edits = create_data_set(doc, self.ln0(doc), "NEW")
        self.assertEqual(len(edits), 1)
        self.assertIsInstance(edits[0], Insert)

    def test_the_element_lands_where_the_content_model_puts_it(self):
        """A7's `reference_for`, which is why this is the first phase since
        then that inserts anything: `DataSet` comes before `ReportControl` in
        `tLN0`'s sequence, so a new one goes ahead of the control blocks and
        not at the end."""
        doc = self.doc()
        doc.apply_edit(create_data_set(doc, self.ln0(doc), "NEW"))
        self.assertEqual([strip_ns(c.tag) for c in self.ln0(doc)],
                         ["DataSet", "DataSet", "DataSet", "ReportControl",
                          "GSEControl", "SampledValueControl"])
        self.assertEqual([d.get("name") for d in iter_local(doc.root, "DataSet")],
                         ["DS1", "DS2", "NEW"])

    def test_a_description_is_written_only_when_given(self):
        doc = self.doc()
        doc.apply_edit(create_data_set(doc, self.ln0(doc), "NEW"))
        self.assertNotIn("desc", _named(doc, "DataSet", "NEW").attrib)
        doc.apply_edit(create_data_set(doc, self.ln0(doc), "TWO", desc="trips"))
        self.assertEqual(_named(doc, "DataSet", "TWO").get("desc"), "trips")

    def test_a_name_is_required(self):
        """The reference's is optional, and filling one in means allocating a
        unique name -- which is A17's, and whose policy its own row in the
        plan calls a likely divergence."""
        doc = self.doc()
        with self.assertRaises(EditRejected) as caught:
            create_data_set(doc, self.ln0(doc), "")
        self.assertIn("A17", str(caught.exception))

    def test_a_name_already_in_the_logical_node_is_refused(self):
        """`DataSetKeyLN0` is an `xs:key`, so two datasets of one name in one
        LN0 is not merely confusing -- it is invalid, and afterwards nothing
        in this library could tell which a `datSet` meant."""
        doc = self.doc()
        with self.assertRaises(EditRejected) as caught:
            create_data_set(doc, self.ln0(doc), "DS1")
        self.assertIn("DS1", str(caught.exception))

    def test_the_same_name_in_another_logical_node_is_fine(self):
        """The key is scoped to the logical node, not to the IED -- so two
        LDevices may each carry a `DS1` and a `datSet` still resolves, because
        61850-6 resolves it in the node the block sits in."""
        second = fx.ldevice("OTHER", fx.ln0())
        publisher = fx.ied("PUB", fx.access_point("S1", fx.ldevice(
            "CFG", fx.ln0(body=fx.dataset("DS1", DS1_MEMBERS))) + second))
        doc = self.doc(fx.scl(fx.header(), publisher))
        nodes = list(iter_local(doc.root, "LN0"))
        doc.apply_edit(create_data_set(doc, nodes[1], "DS1"))
        self.assertEqual([d.get("name") for d in iter_local(doc.root, "DataSet")],
                         ["DS1", "DS1"])

    def test_the_declared_maximum_is_enforced_and_force_skips_it(self):
        doc = self.doc(station(ied_services=fx.services(
            fx.conf_data_set(max_="2"))))
        with self.assertRaises(EditRejected) as caught:
            create_data_set(doc, self.ln0(doc), "NEW")
        message = str(caught.exception)
        self.assertIn("max=2", message)
        self.assertIn("PUB", message)
        self.assertEqual(len(create_data_set(doc, self.ln0(doc), "NEW",
                                             force=True)), 1)

    def test_a_parent_that_may_not_hold_one_is_refused(self):
        doc = self.doc()
        with self.assertRaises(EditRejected):
            create_data_set(doc, next(iter_local(doc.root, "LDevice")), "NEW")
        with self.assertRaises(EditRejected):
            create_data_set(doc, "LN0", "NEW")

    def test_it_is_one_history_entry(self):
        doc = self.doc()
        self.assertRoundTrips(doc, create_data_set(doc, self.ln0(doc), "NEW"))

    def test_the_new_element_carries_the_document_s_namespace(self):
        doc = self.doc()
        doc.apply_edit(create_data_set(doc, self.ln0(doc), "NEW"))
        self.assertEqual(_named(doc, "DataSet", "NEW").tag,
                         self.ln0(doc).tag.replace("LN0", "DataSet"))

    def test_a_document_without_a_namespace_gets_an_element_without_one(self):
        doc = self.doc(station().replace(
            ' xmlns="http://www.iec.ch/61850/2003/SCL"', ""))
        doc.apply_edit(create_data_set(doc, self.ln0(doc), "NEW"))
        self.assertEqual(_named(doc, "DataSet", "NEW").tag, "DataSet")


# -- updateDataSet ----------------------------------------------------------

class TestUpdateDataSet(_Base):
    def rename(self, doc, old, new):
        return update_data_set(doc, SetAttributes(_named(doc, "DataSet", old),
                                                  {"name": new}))

    def test_a_rename_re_points_every_block_that_publishes_it(self):
        """The mirror of Q22. A9 met this event from the control-block side,
        where setting `datSet` renames an exclusively-published dataset; here
        the dataset is renamed directly and every block naming it follows --
        one or several, and DS1 has two."""
        doc = self.doc()
        doc.apply_edit(self.rename(doc, "DS1", "RENAMED"))
        self.assertEqual(_named(doc, "GSEControl", "GCB1").get("datSet"),
                         "RENAMED")
        self.assertEqual(_named(doc, "SampledValueControl", "MSVCB1").get("datSet"),
                         "RENAMED")
        self.assertEqual(_named(doc, "ReportControl", "RCB1").get("datSet"),
                         "DS2")

    def test_leaving_a_block_behind_would_break_the_schema_s_keyref(self):
        """`ref2DataSetGSELN0` is an `xs:keyref` into `DataSetKeyLN0`, so a
        `datSet` naming a dataset that no longer exists is invalid rather than
        merely stale. That is why the re-pointing is not optional."""
        doc = self.doc()
        edits = self.rename(doc, "DS1", "RENAMED")
        self.assertEqual(len(edits), 3)
        doc.apply_edit(edits)
        names = {d.get("name") for d in iter_local(doc.root, "DataSet")}
        for block in iter_local(doc.root, "GSEControl"):
            self.assertIn(block.get("datSet"), names)

    def test_the_revision_does_not_move(self):
        """A rename changes what the dataset is CALLED. The members and their
        order -- what a subscriber cached -- are untouched, so there is
        nothing for a `confRev` to warn about. A9's other half was corrected
        to agree."""
        doc = self.doc()
        doc.apply_edit(self.rename(doc, "DS1", "RENAMED"))
        self.assertEqual(_named(doc, "GSEControl", "GCB1").get("confRev"), "1")

    def test_a_description_change_carries_nothing_with_it(self):
        doc = self.doc()
        edit = SetAttributes(_named(doc, "DataSet", "DS1"), {"desc": "trips"})
        self.assertEqual(update_data_set(doc, edit), [edit])

    def test_setting_the_name_it_already_has_carries_nothing(self):
        doc = self.doc()
        edit = SetAttributes(_named(doc, "DataSet", "DS1"), {"name": "DS1"})
        self.assertEqual(update_data_set(doc, edit), [edit])

    def test_the_name_cannot_be_cleared(self):
        doc = self.doc()
        with self.assertRaises(EditRejected):
            update_data_set(doc, SetAttributes(_named(doc, "DataSet", "DS1"),
                                               {"name": None}))

    def test_a_rename_that_would_collide_is_refused(self):
        doc = self.doc()
        with self.assertRaises(EditRejected) as caught:
            self.rename(doc, "DS1", "DS2")
        message = str(caught.exception)
        self.assertIn("DS1", message)
        self.assertIn("DS2", message)

    def test_a_refusal_changes_nothing(self):
        doc = self.doc()
        before = doc.to_bytes()
        with self.assertRaises(EditRejected):
            self.rename(doc, "DS1", "DS2")
        self.assertEqual(doc.to_bytes(), before)

    def test_the_returned_list_starts_with_the_caller_s_own_edit(self):
        doc = self.doc()
        edit = SetAttributes(_named(doc, "DataSet", "DS1"), {"name": "RENAMED"})
        edits = update_data_set(doc, edit)
        self.assertIs(edits[0], edit)

    def test_it_refuses_anything_that_is_not_a_dataset_edit(self):
        doc = self.doc()
        with self.assertRaises(EditRejected):
            update_data_set(doc, Remove(_named(doc, "DataSet", "DS1")))
        with self.assertRaises(EditRejected):
            update_data_set(doc, SetAttributes(self.ln0(doc), {"name": "X"}))

    def test_it_is_one_history_entry(self):
        doc = self.doc()
        self.assertRoundTrips(doc, self.rename(doc, "DS1", "RENAMED"))

    def test_a_complete_description_still_carries_the_rename(self):
        """Q13's rule -- a mapping naming every attribute is an ordered
        description -- must not hide the `name` this check reads."""
        doc = self.doc()
        data_set = _named(doc, "DataSet", "DS1")
        edit = SetAttributes(data_set, {"name": "RENAMED",
                                        "desc": data_set.get("desc")})
        doc.apply_edit(update_data_set(doc, edit))
        self.assertEqual(_named(doc, "GSEControl", "GCB1").get("datSet"),
                         "RENAMED")


# -- Q19, and who takes what a member publishes -----------------------------

class TestFcdaSubscriptions(_Base):
    def whole(self, doc):
        """DS1's third member: `Ind03`, a data object and no attribute."""
        return _members(_named(doc, "DataSet", "DS1"))[2]

    def test_a_literal_member_finds_the_extref_bound_to_it(self):
        doc = self.doc()
        found = fcda_subscriptions(doc, _members(_named(doc, "DataSet", "DS1"))[0])
        self.assertEqual([e.get("intAddr") for e in found], ["in1"])

    def test_a_whole_object_member_finds_an_attribute_level_extref(self):
        """**Q19, and the reason A9 could close.** `Ind03` publishes every
        attribute of the object, so an `ExtRef` bound to `Ind03.stVal` is
        taking something this member carries -- and a literal comparison of
        the seven attributes cannot see it, because `stVal` against nothing is
        not equal."""
        doc = self.doc()
        found = fcda_subscriptions(doc, self.whole(doc))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].get("doName"), "Ind03")
        self.assertEqual(found[0].get("daName"), "stVal")

    def test_the_relation_does_not_run_the_other_way(self):
        """An FCDA naming ONE attribute does not cover an ExtRef bound to the
        whole object: what travels is `stVal` and the subscriber asked for
        everything. The widening is in one direction only."""
        doc = self.doc()
        ext_ref = next(e for e in iter_local(doc.root, "ExtRef")
                       if e.get("doName") == "Ind03")
        ext_ref.set("daName", "")
        member = _members(_named(doc, "DataSet", "DS1"))[0]
        member.set("doName", "Ind03")
        self.assertFalse(fcda_covers_ext_ref(doc, ext_ref, member))

    def test_a_subscriber_of_another_control_block_is_left_alone(self):
        """`in3` takes `Ind01.stVal`, which is a member of BOTH datasets, but
        it names `RCB1` -- which publishes DS2. Without the control-block
        filter, 254 correct subscriptions across `mixed.scd` and `siemens.scd`
        would be unsubscribed by a removal in a dataset they never used."""
        doc = self.doc()
        found = fcda_subscriptions(doc, _members(_named(doc, "DataSet", "DS1"))[0])
        self.assertNotIn("in3", [e.get("intAddr") for e in found])
        found = fcda_subscriptions(doc, _members(_named(doc, "DataSet", "DS2"))[0])
        self.assertEqual([e.get("intAddr") for e in found], ["in3"])

    def test_an_extref_naming_no_control_block_is_not_filtered_out(self):
        """There is nothing to check it against, and a binding by data
        attributes alone is still a binding."""
        doc = self.doc()
        ext_ref = next(e for e in iter_local(doc.root, "ExtRef")
                       if e.get("intAddr") == "in3")
        del ext_ref.attrib["srcCBName"]
        found = fcda_subscriptions(doc, _members(_named(doc, "DataSet", "DS1"))[0])
        self.assertIn("in3", [e.get("intAddr") for e in found])

    def test_anything_that_is_not_an_fcda_has_no_subscribers(self):
        doc = self.doc()
        self.assertEqual(fcda_subscriptions(doc, self.ln0(doc)), [])
        self.assertEqual(fcda_subscriptions(doc, ET.Element("FCDA")), [])

    def test_the_sweep_and_the_predicate_agree(self):
        """`_subscriptions_to` answers for every member at once, because a
        22 MB export holds 12,540 ExtRefs and a dataset up to 122 members.
        That is an optimisation of `fcda_covers_ext_ref`, and an optimisation
        that drifts from the rule it implements is a bug waiting to be found
        by a file rather than by a test."""
        doc = self.doc()
        for data_set in iter_local(doc.root, "DataSet"):
            publishers = control_blocks(doc, data_set)
            for member in _members(data_set):
                covered = [e for e in iter_local(doc.root, "ExtRef")
                           if fcda_covers_ext_ref(doc, e, member)]
                expected = [e for e in covered
                            if not e.get("srcCBName")
                            or any(b.get("name") == e.get("srcCBName")
                                   for b in publishers)]
                self.assertEqual(fcda_subscriptions(doc, member), expected)

    def test_a_whole_object_member_reads_as_taken(self):
        """`is_subscribed` adopts the wider rule: the member IS consumed --
        what arrives on the wire is the object and the subscriber reads a
        field of it -- so offering it again as unused would be wrong."""
        doc = self.doc()
        subscriber = _named(doc, "IED", "SUB")
        self.assertTrue(is_subscribed(doc, self.whole(doc), subscriber))


# -- removeFCDA -------------------------------------------------------------

class TestRemoveFcda(_Base):
    def members(self, doc, name="DS1"):
        return _members(_named(doc, "DataSet", name))

    def test_a_member_goes_with_its_subscriber(self):
        doc = self.doc()
        member = self.members(doc)[0]
        doc.apply_edit(remove_fcda(doc, Remove(member)))
        self.assertEqual(len(self.members(doc)), 2)
        blanked = next(e for e in iter_local(doc.root, "ExtRef")
                       if e.get("intAddr") == "in1")
        self.assertIsNone(blanked.get("srcCBName"))
        self.assertIsNone(blanked.get("doName"))

    def test_a_whole_object_member_takes_the_attribute_subscription(self):
        """The phase's own goal, in one assertion: the `ExtRef` bound to
        `Ind03.stVal` has no `intAddr`, so unsubscribing REMOVES it -- and a
        literal comparison would have left it bound to data that is gone."""
        doc = self.doc()
        member = self.members(doc)[2]
        self.assertEqual(len(list(iter_local(doc.root, "ExtRef"))), 3)
        doc.apply_edit(remove_fcda(doc, Remove(member)))
        self.assertEqual([e.get("doName") for e in iter_local(doc.root, "ExtRef")],
                         ["Ind01", "Ind01"])

    def test_the_revision_moves_on_every_block_publishing_the_dataset(self):
        """The published data changed, and DS1 has two publishers. A9's
        `updated_conf_rev` is the rule for one block; the fan-out is here,
        because only this side knows the members moved."""
        doc = self.doc()
        doc.apply_edit(remove_fcda(doc, Remove(self.members(doc)[0])))
        step = str(1 + CONF_REV_STEP)
        self.assertEqual(_named(doc, "GSEControl", "GCB1").get("confRev"), step)
        self.assertEqual(_named(doc, "SampledValueControl", "MSVCB1").get("confRev"),
                         step)
        self.assertEqual(_named(doc, "ReportControl", "RCB1").get("confRev"), "1")

    def test_a_caller_can_keep_the_revisions(self):
        doc = self.doc()
        edits = remove_fcda(doc, Remove(self.members(doc)[0]),
                            update_conf_rev=False)
        doc.apply_edit(edits)
        self.assertEqual(_named(doc, "GSEControl", "GCB1").get("confRev"), "1")

    def test_emptying_a_dataset_is_refused(self):
        """`tDataSet`'s content is `<xs:choice maxOccurs="unbounded">` over
        `FCDA` and `minOccurs` defaults to 1, so an emptied dataset is
        invalid; none of the corpus's 295 is empty. The refusal points at the
        capability that means it."""
        doc = self.doc()
        with self.assertRaises(EditRejected) as caught:
            remove_fcda(doc, Remove(self.members(doc, "DS2")[0]))
        message = str(caught.exception)
        self.assertIn("DS2", message)
        self.assertIn("remove the DataSet itself", message)

    def test_a_list_that_takes_every_member_is_refused_as_a_whole(self):
        """Judged one at a time the same set of removals would succeed twice
        and then fail, which makes the answer depend on the order the caller
        happened to ask in."""
        doc = self.doc()
        with self.assertRaises(EditRejected):
            remove_fcda(doc, [Remove(m) for m in self.members(doc)])

    def test_a_list_that_leaves_one_member_is_allowed(self):
        doc = self.doc()
        members = self.members(doc)
        doc.apply_edit(remove_fcda(doc, [Remove(members[0]), Remove(members[1])]))
        self.assertEqual(len(self.members(doc)), 1)

    def test_the_caller_s_own_edits_come_first_and_in_order(self):
        doc = self.doc()
        members = self.members(doc)
        edits = [Remove(members[0]), Remove(members[2])]
        out = remove_fcda(doc, edits)
        self.assertEqual(out[:2], edits)
        self.assertIs(out[0], edits[0])

    def test_members_of_two_datasets_are_handled_together(self):
        doc = self.doc()
        edits = [Remove(self.members(doc)[0]), Remove(self.members(doc, "DS2")[0])]
        with self.assertRaises(EditRejected):
            remove_fcda(doc, edits)          # DS2 would be emptied

    def test_it_refuses_what_is_not_a_member_removal(self):
        doc = self.doc()
        with self.assertRaises(EditRejected):
            remove_fcda(doc, SetAttributes(self.members(doc)[0], {"fc": "MX"}))
        with self.assertRaises(EditRejected):
            remove_fcda(doc, Remove(_named(doc, "DataSet", "DS1")))
        with self.assertRaises(EditRejected) as caught:
            remove_fcda(doc, Remove(ET.SubElement(self.ln0(doc), "FCDA")))
        self.assertIn("outside a DataSet", str(caught.exception))

    def test_supervision_cannot_be_switched_on_yet(self):
        doc = self.doc()
        with self.assertRaises(EditRejected):
            remove_fcda(doc, Remove(self.members(doc)[0]),
                        ignore_supervision=False)

    def test_it_is_one_history_entry(self):
        doc = self.doc()
        self.assertRoundTrips(doc, remove_fcda(doc, Remove(self.members(doc)[2])))


# -- removeDataSet ----------------------------------------------------------

class TestRemoveDataSet(_Base):
    def test_the_publishers_lose_the_reference_and_move_the_revision(self):
        doc = self.doc()
        doc.apply_edit(remove_data_set(doc, Remove(_named(doc, "DataSet", "DS1"))))
        self.assertEqual([d.get("name") for d in iter_local(doc.root, "DataSet")],
                         ["DS2"])
        step = str(1 + CONF_REV_STEP)
        for name, tag in (("GCB1", "GSEControl"), ("MSVCB1", "SampledValueControl")):
            block = _named(doc, tag, name)
            self.assertIsNone(block.get("datSet"))
            self.assertEqual(block.get("confRev"), step)

    def test_a_block_publishing_a_different_dataset_is_untouched(self):
        doc = self.doc()
        doc.apply_edit(remove_data_set(doc, Remove(_named(doc, "DataSet", "DS1"))))
        block = _named(doc, "ReportControl", "RCB1")
        self.assertEqual(block.get("datSet"), "DS2")
        self.assertEqual(block.get("confRev"), "1")

    def test_every_member_s_subscribers_go_including_the_whole_object_one(self):
        doc = self.doc()
        doc.apply_edit(remove_data_set(doc, Remove(_named(doc, "DataSet", "DS1"))))
        remaining = list(iter_local(doc.root, "ExtRef"))
        self.assertEqual([e.get("intAddr") for e in remaining], ["in1", "in3"])
        self.assertIsNone(
            next(e for e in remaining if e.get("intAddr") == "in1").get("doName"))
        self.assertEqual(
            next(e for e in remaining if e.get("intAddr") == "in3").get("doName"),
            "Ind01")

    def test_a_caller_can_keep_the_revisions(self):
        doc = self.doc()
        doc.apply_edit(remove_data_set(doc, Remove(_named(doc, "DataSet", "DS1")),
                                       update_conf_rev=False))
        self.assertEqual(_named(doc, "GSEControl", "GCB1").get("confRev"), "1")
        self.assertIsNone(_named(doc, "GSEControl", "GCB1").get("datSet"))

    def test_a_dataset_nobody_publishes_goes_alone(self):
        """128 `DataSet` elements in the reference corpus are published by no
        control block at all, so this is the ordinary case and not an edge."""
        doc = self.doc(station(ln0_body=fx.dataset("ORPHAN", DS2_MEMBERS)))
        edits = remove_data_set(doc, Remove(_named(doc, "DataSet", "ORPHAN")))
        self.assertEqual(len(edits), 1)

    def test_the_caller_s_own_edit_comes_first(self):
        doc = self.doc()
        edit = Remove(_named(doc, "DataSet", "DS1"))
        self.assertIs(remove_data_set(doc, edit)[0], edit)

    def test_it_refuses_what_is_not_a_dataset_removal(self):
        doc = self.doc()
        with self.assertRaises(EditRejected):
            remove_data_set(doc, SetAttributes(_named(doc, "DataSet", "DS1"),
                                               {"desc": "x"}))
        with self.assertRaises(EditRejected):
            remove_data_set(doc, Remove(self.ln0(doc)))
        with self.assertRaises(EditRejected):
            remove_data_set(doc, Remove(_named(doc, "DataSet", "DS1")),
                            ignore_supervision=False)

    def test_it_is_one_history_entry(self):
        doc = self.doc()
        self.assertRoundTrips(
            doc, remove_data_set(doc, Remove(_named(doc, "DataSet", "DS1"))))


# -- the confRev fan-out ----------------------------------------------------

class TestUpdatedConfRevEdits(_Base):
    def test_one_edit_per_publisher(self):
        doc = self.doc()
        edits = updated_conf_rev_edits(doc, _named(doc, "DataSet", "DS1"))
        self.assertEqual({e.element.get("name") for e in edits},
                         {"GCB1", "MSVCB1"})
        self.assertEqual([e.attributes["confRev"] for e in edits],
                         [str(1 + CONF_REV_STEP)] * 2)

    def test_a_block_can_be_excluded(self):
        doc = self.doc()
        block = _named(doc, "GSEControl", "GCB1")
        edits = updated_conf_rev_edits(doc, _named(doc, "DataSet", "DS1"),
                                       exclude=(block,))
        self.assertEqual([e.element.get("name") for e in edits], ["MSVCB1"])

    def test_an_unpublished_dataset_has_nothing_to_move(self):
        doc = self.doc()
        self.assertEqual(
            updated_conf_rev_edits(doc, _named(doc, "DataSet", "DS2")),
            [SetAttributes(_named(doc, "ReportControl", "RCB1"),
                           {"confRev": str(1 + CONF_REV_STEP)})])


# -- the seam with A9 -------------------------------------------------------

class TestSeamWithRemoveControlBlock(_Base):
    """A9 removes an exclusively-published `DataSet` with a bare `Remove`; A10
    owns what that removal drags along. Two paths that remove a dataset
    differently is the drift Q23 was written to stop, so A9 delegates."""

    def doc(self, text=None):
        """The same station with ONE publisher for DS1, so it is exclusive and
        A9's step 3 is reached at all."""
        return super().doc(text if text is not None else station(ln0_body=(
            fx.dataset("DS1", DS1_MEMBERS)
            + fx.dataset("DS2", DS2_MEMBERS)
            + fx.report_control("RCB1", "DS2")
            + fx.gse_control("GCB1", "DS1"))))

    def test_removing_the_block_also_takes_the_whole_object_subscriber(self):
        """A9 finds subscribers through the `src*` attributes, which catches
        `in1` and the `Ind03.stVal` ExtRef alike because both name `GCB1`.
        What delegation adds is that the DataSet side agrees about which those
        are -- and the ExtRef bound by data attributes alone, which A9's own
        sweep cannot see, now goes with the dataset."""
        doc = self.doc()
        ext_ref = next(e for e in iter_local(doc.root, "ExtRef")
                       if e.get("doName") == "Ind03")
        del ext_ref.attrib["srcCBName"]
        doc.apply_edit(remove_control_block(
            doc, Remove(_named(doc, "GSEControl", "GCB1"))))
        # `in1` had an intAddr, so it is blanked and kept; the `Ind03` ExtRef
        # had none and is gone; `in3` subscribes to DS2 and is untouched.
        self.assertEqual([e.get("doName") for e in iter_local(doc.root, "ExtRef")],
                         [None, "Ind01"])

    def test_no_extref_is_unsubscribed_twice(self):
        """A9 unsubscribes by `src*` and A10 by data attributes, and the two
        sets overlap. A duplicated `Remove` of one `ExtRef` would be a
        double-removal; the ExtRefs A9 handled are passed along and skipped."""
        doc = self.doc()
        edits = remove_control_block(doc, Remove(_named(doc, "GSEControl", "GCB1")))
        removed = [e.node for e in edits if isinstance(e, Remove)]
        self.assertEqual(len(removed), len({id(n) for n in removed}))
        set_on = [e.element for e in edits if isinstance(e, SetAttributes)]
        self.assertEqual(len(set_on), len({id(e) for e in set_on}))

    def test_the_removed_block_is_not_re_pointed_or_re_revised(self):
        """Clearing `datSet` on a block that is being removed in the same
        compound edit would be an edit to an orphan."""
        doc = self.doc()
        block = _named(doc, "GSEControl", "GCB1")
        edits = remove_control_block(doc, Remove(block))
        self.assertEqual([e for e in edits
                          if isinstance(e, SetAttributes) and e.element is block],
                         [])

    def test_it_is_still_one_history_entry(self):
        doc = self.doc()
        self.assertRoundTrips(doc, remove_control_block(
            doc, Remove(_named(doc, "GSEControl", "GCB1"))))


# -- the reference corpus ---------------------------------------------------

class TestCorpus(unittest.TestCase):
    """Vendor exports, where a fixture can only agree with the code that made
    it. `siemens.scd` is the working document -- 68 datasets, 1,603 members,
    122 ms to parse -- and `sel.scd` is read once, because it is the only file
    in the corpus that carries Q19's case at all.
    """

    def corpus(self, name):
        path = roundtrip.CORPUS / name
        if not path.is_file():
            self.skipTest("the corpus is not in this distribution")
        return SclDocument.parse(path)

    def test_not_one_scl_dataset_in_the_corpus_is_empty(self):
        """`tDataSet` requires at least one `FCDA`, and three independent
        vendor tools agree: 295 datasets, none empty, the smallest holding
        one member. It is what `remove_fcda`'s refusal is built on.

        The 127 EMPTY elements also called `DataSet` are in a Siemens
        namespace inside a `Private`, under `GooseApplication` and
        `SMVApplication`. They are not datasets, which is why nothing in this
        module counts by local name.
        """
        totals = {}
        for name in ("siemens.scd", "mixed.scd"):
            doc = self.corpus(name)
            scl, private, empty = 0, 0, 0
            namespace = doc.root.tag[:doc.root.tag.index("}") + 1]
            for element in iter_local(doc.root, "DataSet"):
                if element.tag.startswith(namespace):
                    scl += 1
                    if not _members(element):
                        empty += 1
                else:
                    private += 1
            totals[name] = (scl, empty, private)
        self.assertEqual(totals, {"siemens.scd": (68, 0, 14),
                                  "mixed.scd": (163, 0, 113)})

    def test_every_ied_declares_a_limit_and_none_is_near_it(self):
        """All 58 corpus IEDs carry a `ConfDataSet`, every one on the
        IED-level `Services` -- not one of `mixed.scd`'s 24 AccessPoint-level
        `Services` elements has one. So the limits are real material and only
        the BREACH is synthetic, which is the opposite of A16's problem.
        """
        doc = self.corpus("mixed.scd")
        checked = 0
        for ied in iter_local(doc.root, "IED"):
            for node in iter_local(ied, "LN0"):
                self.assertTrue(can_add_data_set(doc, node), ied.get("name"))
            for data_set in iter_local(ied, "DataSet"):
                if strip_ns(data_set.tag) != "DataSet":
                    continue
                limit = max_attributes(doc, data_set)
                if limit is None:
                    continue
                self.assertEqual(limit.scope, "IED")
                self.assertTrue(can_add_fcda(doc, data_set))
                checked += 1
        self.assertEqual(checked, 163)

    def test_the_whole_object_subscriptions_are_four_and_they_are_in_sel(self):
        """**Q19, measured rather than argued.** Restricted to the dataset the
        subscribed control block actually publishes, `sel.scd` carries four
        subscriptions bound to one attribute of a member published whole --
        two `ASV4 GGIO1.Ind04.stVal` and two `PSV2 GGIO1.Ind13.stVal` -- and
        the other two exports carry none.

        The restriction is half the answer. Swept document-wide instead, the
        same question reports 180 in `mixed.scd` and 74 in `siemens.scd`; every
        one of those also has a literal member match in the dataset it really
        subscribes to, so they are whole-object members of datasets those
        ExtRefs never used. Unsubscribing 254 correct subscriptions is what a
        removal would do if `fcda_subscriptions` ignored the control block.
        """
        found = {}
        for name in ("sel.scd", "mixed.scd", "siemens.scd"):
            doc = self.corpus(name)
            hits = 0
            for data_set in iter_local(doc.root, "DataSet"):
                if strip_ns(data_set.tag) != "DataSet":
                    continue
                whole = [m for m in _members(data_set) if not m.get("daName")]
                if not whole:
                    continue
                # One sweep per dataset rather than one per member: this is
                # what `fcda_subscriptions` calls, and 3,417 whole-object
                # members against 19,622 ExtRefs is a minute of the suite
                # asked member by member. `test_the_sweep_and_the_predicate
                # _agree` is what pins the two to each other.
                hits += len([e for e in _subscriptions_to(doc, whole)
                             if e.get("daName")])
            found[name] = hits
        self.assertEqual(found, {"sel.scd": 4, "mixed.scd": 0, "siemens.scd": 0})

    def test_a_real_dataset_is_renamed_and_undone_byte_for_byte(self):
        """Every control block that published it follows the name, and the
        revisions do not move -- the same members in the same order."""
        doc = self.corpus("siemens.scd")
        original = doc.to_bytes()
        data_set = next(d for d in iter_local(doc.root, "DataSet")
                        if strip_ns(d.tag) == "DataSet" and control_blocks(doc, d))
        publishers = control_blocks(doc, data_set)
        revisions = [b.get("confRev") for b in publishers]

        undo = doc.apply_edit(update_data_set(
            doc, SetAttributes(data_set, {"name": "A10_RENAMED"})))
        for block in publishers:
            self.assertEqual(block.get("datSet"), "A10_RENAMED")
        self.assertEqual([b.get("confRev") for b in publishers], revisions)

        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), original)

    def test_a_real_dataset_is_removed_with_everything_it_publishes(self):
        """The phase's goal on vendor material: take a dataset with members
        and subscribers, remove it with every reference to it repaired, and
        get the file back byte for byte on the undo."""
        doc = self.corpus("siemens.scd")
        original = doc.to_bytes()
        data_set, subscribers = None, []
        for candidate in iter_local(doc.root, "DataSet"):
            if strip_ns(candidate.tag) != "DataSet" or not control_blocks(doc, candidate):
                continue
            taken = [e for member in _members(candidate)
                     for e in fcda_subscriptions(doc, member)]
            if taken:
                data_set, subscribers = candidate, taken
                break
        self.assertIsNotNone(data_set)
        self.assertGreater(len(subscribers), 1)
        publishers = control_blocks(doc, data_set)

        undo = doc.apply_edit(remove_data_set(doc, Remove(data_set)))

        self.assertIsNone(doc.parent_of(data_set))
        for block in publishers:
            self.assertIsNone(block.get("datSet"))
            self.assertEqual(int(block.get("confRev")) % CONF_REV_STEP, 1)
        for ext_ref in subscribers:
            self.assertTrue(doc.parent_of(ext_ref) is None
                            or not ext_ref.get("iedName"))

        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), original)

    def test_a_real_member_is_removed_and_undone_byte_for_byte(self):
        doc = self.corpus("siemens.scd")
        original = doc.to_bytes()
        data_set = next(d for d in iter_local(doc.root, "DataSet")
                        if strip_ns(d.tag) == "DataSet" and len(_members(d)) > 1)
        member = _members(data_set)[0]
        undo = doc.apply_edit(remove_fcda(doc, Remove(member)))
        self.assertNotIn(member, _members(data_set))
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), original)

    def test_a_created_dataset_lands_in_the_right_place_in_a_real_file(self):
        """The content-model position, checked against a document a vendor
        wrote rather than one a fixture built: the new element goes after the
        datasets already there and before the first control block."""
        doc = self.corpus("siemens.scd")
        original = doc.to_bytes()
        node = next(n for n in iter_local(doc.root, "LN0")
                    if [c for c in n if strip_ns(c.tag) == "DataSet"])
        undo = doc.apply_edit(create_data_set(doc, node, "A10_NEW"))
        tags = [strip_ns(c.tag) for c in node]
        at = tags.index("DataSet") + tags.count("DataSet") - 1
        self.assertEqual(node[at].get("name"), "A10_NEW")
        # Everything before it is a DataSet or one of the elements `tLN0`
        # puts ahead of them; everything after is a control block or later.
        self.assertTrue(set(tags[:at]) <= {"DataSet", "Private", "Text"}, tags[:at])
        self.assertNotIn("DataSet", tags[at + 1:])
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), original)


class TestDegenerateMembers(_Base):
    """`doName` is optional on `tFCDA`, and 5 of `siemens.scd`'s 1,603 members
    leave it off -- the same 5 `fcda_type` cannot resolve."""

    def test_a_member_naming_no_data_object_covers_nothing(self):
        doc = self.doc()
        member = _members(_named(doc, "DataSet", "DS1"))[2]
        del member.attrib["doName"]
        ext_ref = next(iter_local(doc.root, "ExtRef"))
        del ext_ref.attrib["doName"]
        del ext_ref.attrib["daName"]
        self.assertFalse(fcda_covers_ext_ref(doc, ext_ref, member))
        self.assertEqual(fcda_subscriptions(doc, member), [])

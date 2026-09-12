# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""`py61850.scl.control_block` -- the queries, the two edit checks, and what a
removal drags with it.

The built fixtures carry the cases the corpus does not: a `DataSet` published
by two control blocks, a `LogControl`, a rename that collides. Everything the
corpus DOES carry is asserted against the corpus instead, at the bottom of the
file, because a fixture agreeing with the code that built it proves less than
a vendor export agreeing with it.
"""

import tempfile
import unittest
from xml.etree import ElementTree as ET

from py61850.scl import (
    CONF_REV_STEP,
    EditRejected,
    Remove,
    SclDocument,
    SetAttributes,
    control_block_gse_or_smv,
    control_block_obj_ref,
    control_blocks,
    find_control_block_subscription,
    iter_local,
    path_id,
    remove_control_block,
    strip_ns,
    update_dat_set,
    updated_conf_rev,
)
from py61850.scl.control_block import CONTROL_BLOCK_TAGS
from tests.unit import roundtrip
from tests.unit import scl_fixtures as fx


def _named(doc, tag, name):
    """The one element with this local name and `name` attribute."""
    found = [el for el in iter_local(doc.root, tag) if el.get("name") == name]
    assert len(found) == 1, f"{tag} {name!r}: {len(found)} found"
    return found[0]


def _tags(doc, tag):
    return [el for el in iter_local(doc.root, tag)]


MEMBERS = (fx.fcda("CFG", "GGIO", "Ind01", "ST", da_name="stVal"),
           fx.fcda("CFG", "GGIO", "Ind02", "ST", da_name="stVal"))


def station(ln0_body=None, ap_body=None, subscriber=True):
    """A two-IED station: `PUB` publishes, `SUB` listens.

    `PUB` holds one `LDevice` `CFG` whose `LN0` carries two datasets and the
    control blocks that publish them, and the `Communication` section
    addresses its GOOSE and sampled-value blocks 1:1, as every corpus file
    does.
    """
    if ln0_body is None:
        ln0_body = (fx.dataset("DS1", MEMBERS)
                    + fx.dataset("DS2", MEMBERS)
                    + fx.report_control("RCB1", "DS2")
                    + fx.report_control("TEMPLATE",
                                        body=fx.private("Vendor-Predefined"))
                    + fx.gse_control("GCB1", "DS1", app_id="APP/GCB1")
                    + fx.smv_control("MSVCB1", "DS2"))
    publisher = fx.ied("PUB", fx.access_point(
        "S1", fx.ldevice("CFG", fx.ln0(body=ln0_body))))
    if ap_body is None:
        ap_body = (fx.gse("CFG", "GCB1", fx.address(P_MAC_Address="01-0C-CD-01-00-01"))
                   + fx.smv("CFG", "MSVCB1", fx.address(P_APPID="4000")))
    body = [fx.header(), publisher]
    if subscriber:
        body.append(fx.ied("SUB", fx.access_point("S1", fx.ldevice("PROT", fx.ln0(
            body=fx.inputs(
                # Later binding: the IED published the input, so unsubscribing
                # blanks it and keeps the element.
                fx.ext_ref(intAddr="in1", iedName="PUB", ldInst="CFG",
                           lnClass="GGIO", doName="Ind01", daName="stVal",
                           serviceType="GOOSE", srcCBName="GCB1",
                           srcLDInst="CFG", srcLNClass="LLN0"),
                # No intAddr: the element exists only because of the
                # connection, so unsubscribing removes it.
                fx.ext_ref(iedName="PUB", ldInst="CFG", lnClass="GGIO",
                           doName="Ind02", daName="stVal",
                           serviceType="GOOSE", srcCBName="GCB1",
                           srcLDInst="CFG", srcLNClass="LLN0"),
                # A subscriber to a different block, which nothing here
                # should touch.
                fx.ext_ref(intAddr="in3", iedName="PUB", ldInst="CFG",
                           lnClass="GGIO", doName="Ind01", daName="stVal",
                           serviceType="Report", srcCBName="RCB1",
                           srcLDInst="CFG", srcLNClass="LLN0")))))))
    body.append(fx.communication(
        fx.subnetwork("W1", [fx.connected_ap("PUB", "S1", ap_body)])))
    return fx.scl(*body)


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._written = 0

    def doc(self, text=None):
        """A parsed `SclDocument` over `text`, defaulting to :func:`station`.

        Through a file, as `test_scl_extref` does: `SclDocument` carries a
        byte-fidelity guarantee that only exists for a document it parsed, and
        several assertions below compare `to_bytes` against itself.
        """
        self._written += 1
        return SclDocument.parse(fx.write(
            self._tmp.name, f"station{self._written}.scd",
            text if text is not None else station()))


# -- controlBlocks ----------------------------------------------------------

class TestControlBlocks(_Base):
    def test_a_dataset_answers_with_what_publishes_it(self):
        doc = self.doc()
        blocks = control_blocks(doc, _named(doc, "DataSet", "DS1"))
        self.assertEqual([b.get("name") for b in blocks], ["GCB1"])

    def test_a_member_answers_the_same_as_its_dataset(self):
        """An `FCDA` is published by whatever publishes the dataset holding
        it, and it is resolved through its PARENT -- so nothing here compares
        `doName` against anything and Q19's whole-data-object case cannot
        arise."""
        doc = self.doc()
        data_set = _named(doc, "DataSet", "DS2")
        member = next(iter_local(data_set, "FCDA"))
        self.assertEqual(control_blocks(doc, member),
                         control_blocks(doc, data_set))
        self.assertEqual([b.get("name") for b in control_blocks(doc, member)],
                         ["RCB1", "MSVCB1"])

    def test_the_answer_is_in_file_order(self):
        doc = self.doc()
        blocks = control_blocks(doc, _named(doc, "DataSet", "DS2"))
        self.assertEqual([strip_ns(b.tag) for b in blocks],
                         ["ReportControl", "SampledValueControl"])

    def test_a_dataset_nobody_publishes_answers_empty(self):
        """128 `DataSet` elements in the reference corpus are published by
        nothing. An empty list is an ordinary answer, not a failure."""
        doc = self.doc(station(ln0_body=fx.dataset("ORPHAN", MEMBERS)))
        self.assertEqual(control_blocks(doc, _named(doc, "DataSet", "ORPHAN")),
                         [])

    def test_a_log_control_counts(self):
        doc = self.doc(station(ln0_body=(fx.dataset("DS1", MEMBERS)
                                         + fx.log_control("LOG1", "DS1"))))
        self.assertEqual(
            [b.get("name") for b in control_blocks(doc, _named(doc, "DataSet", "DS1"))],
            ["LOG1"])

    def test_a_block_naming_no_dataset_publishes_nothing(self):
        doc = self.doc()
        for blocks in (control_blocks(doc, _named(doc, "DataSet", "DS1")),
                       control_blocks(doc, _named(doc, "DataSet", "DS2"))):
            self.assertNotIn("TEMPLATE", [b.get("name") for b in blocks])

    def test_anything_else_answers_empty(self):
        doc = self.doc()
        self.assertEqual(control_blocks(doc, _named(doc, "IED", "PUB")), [])
        self.assertEqual(control_blocks(doc, None), [])
        self.assertEqual(control_blocks(doc, ET.Element("FCDA")), [])


# -- findControlBlockSubscription -------------------------------------------

class TestFindControlBlockSubscription(_Base):
    def test_the_subscribers_of_one_block(self):
        doc = self.doc()
        found = find_control_block_subscription(doc, _named(doc, "GSEControl", "GCB1"))
        self.assertEqual([e.get("doName") for e in found], ["Ind01", "Ind02"])

    def test_a_subscriber_to_another_block_is_not_returned(self):
        doc = self.doc()
        found = find_control_block_subscription(doc, _named(doc, "ReportControl", "RCB1"))
        self.assertEqual([e.get("intAddr") for e in found], ["in3"])

    def test_the_publisher_has_to_match_too(self):
        """`srcCBName` alone is not enough: two IEDs may each hold an `LN0`
        with the same logical-device instance and the same block name, and an
        `ExtRef` says which one it means with `iedName`."""
        doc = self.doc()
        for ext_ref in iter_local(doc.root, "ExtRef"):
            doc.apply_edit(SetAttributes(ext_ref, {"iedName": "SOMEONE_ELSE"}))
        self.assertEqual(
            find_control_block_subscription(doc, _named(doc, "GSEControl", "GCB1")),
            [])

    def test_an_ied_name_child_is_not_a_subscription(self):
        """345 `IEDName` children are in the reference corpus and none is
        returned here. They are Edition 1's record of a subscriber, they are
        CHILDREN of the control block so a removal takes them with it, and no
        corpus block records a subscriber only that way."""
        doc = self.doc(station(ln0_body=(
            fx.dataset("DS1", MEMBERS)
            + fx.gse_control("GCB1", "DS1",
                             body=fx.ied_name("SUB", ap_ref="S1")))))
        block = _named(doc, "GSEControl", "GCB1")
        self.assertEqual(len(list(iter_local(block, "IEDName"))), 1)
        found = find_control_block_subscription(doc, block)
        self.assertTrue(all(strip_ns(e.tag) == "ExtRef" for e in found))

    def test_anything_that_is_not_a_control_block_answers_empty(self):
        doc = self.doc()
        self.assertEqual(find_control_block_subscription(
            doc, _named(doc, "DataSet", "DS1")), [])


# -- controlBlockObjRef and pathId ------------------------------------------

class TestControlBlockObjRef(_Base):
    def test_the_seven_two_object_reference(self):
        doc = self.doc()
        self.assertEqual(
            control_block_obj_ref(doc, _named(doc, "GSEControl", "GCB1")),
            "PUBCFG/LLN0.GCB1")

    def test_every_kind_answers(self):
        doc = self.doc()
        self.assertEqual(
            control_block_obj_ref(doc, _named(doc, "ReportControl", "RCB1")),
            "PUBCFG/LLN0.RCB1")
        self.assertEqual(
            control_block_obj_ref(doc, _named(doc, "SampledValueControl", "MSVCB1")),
            "PUBCFG/LLN0.MSVCB1")

    def test_an_element_outside_an_ied_has_no_reference(self):
        doc = self.doc()
        self.assertIsNone(control_block_obj_ref(doc, ET.Element("GSEControl")))
        self.assertIsNone(control_block_obj_ref(doc, _named(doc, "DataSet", "DS1")))


class TestPathId(_Base):
    def test_the_identifier_a_new_block_would_take(self):
        """Slashes throughout, and computable before the block exists -- which
        is the point of taking a name rather than an element."""
        doc = self.doc()
        ln0 = next(iter_local(_named(doc, "IED", "PUB"), "LN0"))
        self.assertEqual(path_id(doc, ln0, "NEW"), "PUB/CFG/LLN0/NEW")

    def test_it_is_not_the_object_reference(self):
        doc = self.doc()
        ln0 = next(iter_local(_named(doc, "IED", "PUB"), "LN0"))
        self.assertNotEqual(
            path_id(doc, ln0, "GCB1"),
            control_block_obj_ref(doc, _named(doc, "GSEControl", "GCB1")))

    def test_it_needs_a_logical_node(self):
        doc = self.doc()
        self.assertIsNone(path_id(doc, _named(doc, "IED", "PUB"), "NEW"))
        self.assertIsNone(path_id(doc, None, "NEW"))


# -- controlBlockGseOrSmv ---------------------------------------------------

class TestControlBlockGseOrSmv(_Base):
    def test_a_gse_control_finds_its_gse(self):
        doc = self.doc()
        address = control_block_gse_or_smv(doc, _named(doc, "GSEControl", "GCB1"))
        self.assertIsNotNone(address)
        self.assertEqual(strip_ns(address.tag), "GSE")
        self.assertEqual(address.get("cbName"), "GCB1")

    def test_a_sampled_value_control_finds_its_smv(self):
        doc = self.doc()
        address = control_block_gse_or_smv(doc, _named(doc, "SampledValueControl", "MSVCB1"))
        self.assertEqual(strip_ns(address.tag), "SMV")

    def test_a_report_control_has_no_address_of_its_own(self):
        """A report travels over the IED's own MMS association; only GOOSE and
        sampled values have a link-layer address in `Communication`."""
        doc = self.doc()
        self.assertIsNone(
            control_block_gse_or_smv(doc, _named(doc, "ReportControl", "RCB1")))

    def test_every_access_point_of_the_ied_is_searched(self):
        """12 IEDs in the mixed-vendor reference station carry more than one
        `ConnectedAP`, and the address may be under either."""
        doc = self.doc(station(ap_body=""))
        subnet = next(iter_local(doc.root, "SubNetwork"))
        second = ET.SubElement(subnet, "{%s}ConnectedAP" % fx.SCL_NS,
                               {"iedName": "PUB", "apName": "S2"})
        ET.SubElement(second, "{%s}GSE" % fx.SCL_NS,
                      {"ldInst": "CFG", "cbName": "GCB1"})
        address = control_block_gse_or_smv(doc, _named(doc, "GSEControl", "GCB1"))
        self.assertIsNotNone(address)

    def test_a_missing_address_is_not_an_error(self):
        doc = self.doc(station(ap_body=""))
        self.assertIsNone(
            control_block_gse_or_smv(doc, _named(doc, "GSEControl", "GCB1")))


# -- updatedConfRev ---------------------------------------------------------

class TestUpdatedConfRev(unittest.TestCase):
    def test_the_step_is_ten_thousand(self):
        self.assertEqual(CONF_REV_STEP, 10000)
        self.assertEqual(updated_conf_rev(ET.Element("X", {"confRev": "1"})),
                         "10001")

    def test_it_continues_a_vendor_encoded_sequence(self):
        """`850001` is not an opaque vendor number -- it is `1 + 85 x 10000`,
        and every one of the 1,014 corpus values congruent to 1 modulo 10,000
        was produced this way. A step of 1 would be indistinguishable from
        them; this one continues the sequence the file is already writing."""
        self.assertEqual(updated_conf_rev(ET.Element("X", {"confRev": "850001"})),
                         "860001")
        self.assertEqual(updated_conf_rev(ET.Element("X", {"confRev": "1890001"})),
                         "1900001")

    def test_a_block_with_no_revision_starts_at_one_step(self):
        """Not at zero: a `confRev` of `0` reads as 'never configured'."""
        self.assertEqual(updated_conf_rev(ET.Element("X")), "10000")
        self.assertEqual(updated_conf_rev(ET.Element("X", {"confRev": ""})), "10000")

    def test_a_revision_that_is_not_a_number_is_left_alone(self):
        """It is a vendor's encoding. Renumbering it would be this library
        inventing a revision nobody downstream can interpret."""
        self.assertEqual(updated_conf_rev(ET.Element("X", {"confRev": "A.7"})),
                         "A.7")

    def test_it_returns_a_value_and_not_an_edit(self):
        block = ET.Element("GSEControl", {"confRev": "1"})
        updated_conf_rev(block)
        self.assertEqual(block.get("confRev"), "1")


# -- updateDatSet -----------------------------------------------------------

class TestUpdateDatSet(_Base):
    def edits(self, doc, block_name, attributes, tag="GSEControl"):
        return update_dat_set(doc, SetAttributes(_named(doc, tag, block_name),
                                                 attributes))

    def test_an_exclusive_dataset_is_renamed_with_the_reference(self):
        """The surprising one, and it is the reference's behaviour: a dataset
        only this block publishes has no identity of its own, so renaming the
        block's `datSet` renames the dataset the engineer can see."""
        doc = self.doc()
        edits = self.edits(doc, "GCB1", {"datSet": "RENAMED"})
        doc.apply_edit(edits)
        self.assertEqual(_named(doc, "GSEControl", "GCB1").get("datSet"), "RENAMED")
        self.assertEqual([d.get("name") for d in _tags(doc, "DataSet")],
                         ["RENAMED", "DS2"])

    def test_a_rename_does_not_move_the_revision(self):
        """The data the subscriber cached is unchanged -- the same members in
        the same order, under another name -- so there is nothing for a
        `confRev` to warn about. A10 took the same view from the DataSet side
        and this side was corrected to agree; see Q19's neighbour in the
        module docstring."""
        doc = self.doc()
        doc.apply_edit(self.edits(doc, "GCB1", {"datSet": "RENAMED"}))
        self.assertEqual(_named(doc, "GSEControl", "GCB1").get("confRev"), "1")

    def test_a_genuine_re_pointing_does_move_the_revision(self):
        """A block whose dataset is SHARED cannot be renamed into, so setting
        `datSet` re-points it at different data -- and that is what a
        subscriber has to be told about."""
        doc = self.doc(station(ln0_body=(
            fx.dataset("SHARED", MEMBERS)
            + fx.gse_control("GCB1", "SHARED")
            + fx.report_control("RCB1", "SHARED"))))
        doc.apply_edit(self.edits(doc, "GCB1", {"datSet": "OTHER"}))
        self.assertEqual(_named(doc, "GSEControl", "GCB1").get("confRev"),
                         str(1 + CONF_REV_STEP))

    def test_a_shared_dataset_is_not_renamed(self):
        """No dataset in the reference corpus is shared, so this branch is
        built rather than found -- and it is kept because the schema permits
        the sharing and renaming would rewrite what another block publishes."""
        doc = self.doc(station(ln0_body=(
            fx.dataset("SHARED", MEMBERS)
            + fx.gse_control("GCB1", "SHARED")
            + fx.report_control("RCB1", "SHARED"))))
        doc.apply_edit(self.edits(doc, "GCB1", {"datSet": "OTHER"}))
        self.assertEqual([d.get("name") for d in _tags(doc, "DataSet")], ["SHARED"])
        self.assertEqual(_named(doc, "ReportControl", "RCB1").get("datSet"), "SHARED")

    def test_a_rename_that_would_collide_is_refused(self):
        doc = self.doc()
        with self.assertRaises(EditRejected) as caught:
            self.edits(doc, "GCB1", {"datSet": "DS2"})
        self.assertIn("DS1", str(caught.exception))
        self.assertIn("DS2", str(caught.exception))

    def test_a_refused_rename_changes_nothing(self):
        doc = self.doc()
        before = doc.to_bytes()
        with self.assertRaises(EditRejected):
            self.edits(doc, "GCB1", {"datSet": "DS2"})
        self.assertEqual(doc.to_bytes(), before)

    def test_unlinking_renames_nothing_but_still_moves_the_revision(self):
        doc = self.doc()
        doc.apply_edit(self.edits(doc, "GCB1", {"datSet": None}))
        self.assertIsNone(_named(doc, "GSEControl", "GCB1").get("datSet"))
        self.assertEqual([d.get("name") for d in _tags(doc, "DataSet")],
                         ["DS1", "DS2"])
        self.assertEqual(_named(doc, "GSEControl", "GCB1").get("confRev"), "10001")

    def test_a_block_that_named_no_dataset_gains_one(self):
        """720 corpus blocks start here. There is no dataset to rename, so the
        reference is simply set and the revision moves."""
        doc = self.doc()
        edits = self.edits(doc, "TEMPLATE", {"datSet": "DS2"}, tag="ReportControl")
        doc.apply_edit(edits)
        self.assertEqual(_named(doc, "ReportControl", "TEMPLATE").get("datSet"), "DS2")
        self.assertEqual([d.get("name") for d in _tags(doc, "DataSet")],
                         ["DS1", "DS2"])

    def test_setting_the_same_value_is_not_a_change(self):
        """A no-op must not burn a revision: a subscriber would re-learn the
        dataset for nothing."""
        doc = self.doc()
        edits = self.edits(doc, "GCB1", {"datSet": "DS1"})
        self.assertEqual(len(edits), 1)
        doc.apply_edit(edits)
        self.assertEqual(_named(doc, "GSEControl", "GCB1").get("confRev"), "1")

    def test_an_edit_that_does_not_touch_datset_comes_back_alone(self):
        doc = self.doc()
        edits = self.edits(doc, "GCB1", {"desc": "the bus GOOSE"})
        self.assertEqual(len(edits), 1)
        doc.apply_edit(edits)
        self.assertEqual(_named(doc, "GSEControl", "GCB1").get("confRev"), "1")

    def test_a_caller_that_names_the_revision_keeps_it(self):
        """The reference's `create*Control` says the same in its own words: a
        user-supplied `confRev` overwrites the logic that would set it. Asked
        on the re-pointing path, which is the one that would otherwise move
        it."""
        doc = self.doc(station(ln0_body=(
            fx.dataset("SHARED", MEMBERS)
            + fx.gse_control("GCB1", "SHARED")
            + fx.report_control("RCB1", "SHARED"))))
        edits = self.edits(doc, "GCB1", {"datSet": "OTHER", "confRev": "7"})
        self.assertEqual(len(edits), 1)
        doc.apply_edit(edits)
        self.assertEqual(_named(doc, "GSEControl", "GCB1").get("confRev"), "7")

    def test_the_returned_list_includes_the_caller_s_own_edit(self):
        doc = self.doc()
        block = _named(doc, "GSEControl", "GCB1")
        edit = SetAttributes(block, {"datSet": "RENAMED"})
        edits = update_dat_set(doc, edit)
        self.assertIs(edits[0], edit)
        # The caller's edit and the rename. No `confRev`: the published data
        # is the same data under a new name.
        self.assertEqual(len(edits), 2)

    def test_it_is_one_history_entry(self):
        doc = self.doc()
        before = doc.to_bytes()
        undo = doc.apply_edit(self.edits(doc, "GCB1", {"datSet": "RENAMED"}))
        self.assertNotEqual(doc.to_bytes(), before)
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)

    def test_a_complete_description_still_carries_the_rename(self):
        """Q13's rule -- a mapping naming every attribute is an ordered
        description -- must not hide the `datSet` the check reads."""
        doc = self.doc()
        block = _named(doc, "GSEControl", "GCB1")
        complete = dict(block.attrib)
        complete["datSet"] = "RENAMED"
        doc.apply_edit(update_dat_set(doc, SetAttributes(block, complete)))
        self.assertEqual([d.get("name") for d in _tags(doc, "DataSet")],
                         ["RENAMED", "DS2"])

    def test_it_refuses_anything_that_is_not_a_set_on_a_control_block(self):
        doc = self.doc()
        with self.assertRaises(EditRejected):
            update_dat_set(doc, Remove(_named(doc, "GSEControl", "GCB1")))
        with self.assertRaises(EditRejected) as caught:
            update_dat_set(doc, SetAttributes(_named(doc, "DataSet", "DS1"),
                                              {"datSet": "X"}))
        self.assertIn("DataSet", str(caught.exception))


# -- removeControlBlock -----------------------------------------------------

class TestRemoveControlBlock(_Base):
    def remove(self, doc, tag, name, **kwargs):
        return remove_control_block(doc, Remove(_named(doc, tag, name)), **kwargs)

    def test_the_block_goes(self):
        doc = self.doc()
        doc.apply_edit(self.remove(doc, "GSEControl", "GCB1"))
        self.assertEqual(_tags(doc, "GSEControl"), [])

    def test_its_exclusive_dataset_goes_with_it(self):
        doc = self.doc()
        doc.apply_edit(self.remove(doc, "GSEControl", "GCB1"))
        self.assertEqual([d.get("name") for d in _tags(doc, "DataSet")], ["DS2"])

    def test_a_shared_dataset_stays(self):
        doc = self.doc(station(ln0_body=(
            fx.dataset("SHARED", MEMBERS)
            + fx.gse_control("GCB1", "SHARED")
            + fx.report_control("RCB1", "SHARED"))))
        doc.apply_edit(self.remove(doc, "GSEControl", "GCB1"))
        self.assertEqual([d.get("name") for d in _tags(doc, "DataSet")], ["SHARED"])

    def test_a_dataset_a_log_still_writes_stays(self):
        """`LogControl` carries a `datSet` like the other three, so it counts
        as a user. Deleting a dataset a log still writes would silently stop
        the logging."""
        doc = self.doc(station(ln0_body=(
            fx.dataset("DS1", MEMBERS)
            + fx.gse_control("GCB1", "DS1")
            + fx.log_control("LOG1", "DS1"))))
        doc.apply_edit(self.remove(doc, "GSEControl", "GCB1"))
        self.assertEqual([d.get("name") for d in _tags(doc, "DataSet")], ["DS1"])

    def test_its_address_goes_with_it(self):
        """The divergence: the reference documents three other steps and not
        this one. Leaving the `GSE` behind writes a `Communication` section
        naming a control block that is gone. See Q21."""
        doc = self.doc()
        doc.apply_edit(self.remove(doc, "GSEControl", "GCB1"))
        self.assertEqual(_tags(doc, "GSE"), [])
        self.assertEqual(len(_tags(doc, "SMV")), 1)

    def test_a_sampled_value_block_takes_its_smv(self):
        doc = self.doc()
        doc.apply_edit(self.remove(doc, "SampledValueControl", "MSVCB1"))
        self.assertEqual(_tags(doc, "SMV"), [])

    def test_its_subscribers_are_unsubscribed(self):
        doc = self.doc()
        doc.apply_edit(self.remove(doc, "GSEControl", "GCB1"))
        remaining = _tags(doc, "ExtRef")
        # The later-binding one is blanked and kept; the one with no intAddr
        # is removed; the subscriber to RCB1 is untouched.
        self.assertEqual(sorted(e.get("intAddr") for e in remaining), ["in1", "in3"])
        blanked = next(e for e in remaining if e.get("intAddr") == "in1")
        self.assertIsNone(blanked.get("srcCBName"))
        self.assertIsNone(blanked.get("iedName"))
        untouched = next(e for e in remaining if e.get("intAddr") == "in3")
        self.assertEqual(untouched.get("srcCBName"), "RCB1")

    def test_an_emptied_inputs_goes_too(self):
        doc = self.doc(station(ln0_body=(
            fx.dataset("DS1", MEMBERS) + fx.gse_control("GCB1", "DS1"))))
        # The subscriber's only two ExtRefs both name GCB1, and neither the
        # first nor the third of the default set survives here.
        for ext_ref in list(iter_local(doc.root, "ExtRef")):
            if ext_ref.get("srcCBName") != "GCB1" or ext_ref.get("intAddr"):
                doc.apply_edit(Remove(ext_ref))
        self.assertEqual(len(_tags(doc, "Inputs")), 1)
        doc.apply_edit(self.remove(doc, "GSEControl", "GCB1"))
        self.assertEqual(_tags(doc, "Inputs"), [])

    def test_a_template_block_takes_nothing_with_it(self):
        """720 corpus blocks name no dataset. The expansion is the removal
        alone -- no dataset, no address, no subscriber."""
        doc = self.doc()
        edits = self.remove(doc, "ReportControl", "TEMPLATE")
        self.assertEqual(len(edits), 1)
        doc.apply_edit(edits)
        self.assertEqual([d.get("name") for d in _tags(doc, "DataSet")],
                         ["DS1", "DS2"])

    def test_a_log_control_can_be_removed(self):
        doc = self.doc(station(ln0_body=(
            fx.dataset("DS1", MEMBERS) + fx.log_control("LOG1", "DS1"))))
        doc.apply_edit(self.remove(doc, "LogControl", "LOG1"))
        self.assertEqual(_tags(doc, "LogControl"), [])
        self.assertEqual(_tags(doc, "DataSet"), [])

    def test_the_returned_list_includes_the_caller_s_own_edit(self):
        doc = self.doc()
        edit = Remove(_named(doc, "GSEControl", "GCB1"))
        edits = remove_control_block(doc, edit)
        self.assertIs(edits[0], edit)

    def test_it_is_one_history_entry(self):
        """The whole point of the expansion: a removal that took four elements
        and blanked a fifth is one thing to undo."""
        doc = self.doc()
        before = doc.to_bytes()
        undo = doc.apply_edit(self.remove(doc, "GSEControl", "GCB1"))
        self.assertNotEqual(doc.to_bytes(), before)
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)

    def test_supervision_cannot_be_asked_for_yet(self):
        doc = self.doc()
        with self.assertRaises(EditRejected) as caught:
            self.remove(doc, "GSEControl", "GCB1", ignore_supervision=False)
        self.assertIn("supervision", str(caught.exception))

    def test_it_refuses_anything_that_is_not_a_remove_of_a_control_block(self):
        doc = self.doc()
        with self.assertRaises(EditRejected):
            remove_control_block(doc, SetAttributes(
                _named(doc, "GSEControl", "GCB1"), {"desc": "x"}))
        with self.assertRaises(EditRejected) as caught:
            remove_control_block(doc, Remove(_named(doc, "DataSet", "DS1")))
        self.assertIn("DataSet", str(caught.exception))

    def test_a_refusal_changes_nothing(self):
        doc = self.doc()
        before = doc.to_bytes()
        with self.assertRaises(EditRejected):
            self.remove(doc, "GSEControl", "GCB1", ignore_supervision=False)
        self.assertEqual(doc.to_bytes(), before)


# -- the corpus -------------------------------------------------------------

class TestTheCorpusAgrees(unittest.TestCase):
    """What the built fixtures cannot say, because they were built.

    `siemens.scd` is the working document: 122 ms to parse -- the cheapest of
    the three -- and the only one carrying every hazard this phase has at
    once, 360 dataset-less report templates, 77 `IEDName` children, 14 orphan
    datasets and 422 fully bound `ExtRef`s. `mixed.scd` is read for the two
    things siemens has not: the stepped `confRev` values, and the corpus's
    only `SampledValueControl`s.
    """

    def corpus(self, name):
        path = roundtrip.CORPUS / name
        if not path.is_file():
            self.skipTest("the corpus is not in this distribution")
        return SclDocument.parse(path)

    def test_the_object_reference_is_the_one_the_file_writes(self):
        """`setSrcRef` is where a supervision logical node records the control
        block it watches, and it is written as the 7-2 object reference. 74 of
        `siemens.scd`'s values are path-shaped and every one of them is
        reproduced exactly.

        `sel.scd` is deliberately not asserted here: 1,321 of its 1,536
        path-shaped values name the block's DATASET rather than the block --
        `QPC1_LT1_UPC2CFG/LLN0.GOPB_138` against a block called `GoSB00` --
        which is that tool's supervision encoding and a question for the
        phase that writes supervision, not for this one.
        """
        doc = self.corpus("siemens.scd")
        produced = set()
        for element in doc.root.iter():
            if strip_ns(element.tag) in CONTROL_BLOCK_TAGS:
                reference = control_block_obj_ref(doc, element)
                if reference:
                    produced.add(reference)
        written = []
        for dai in iter_local(doc.root, "DAI"):
            if dai.get("name") != "setSrcRef":
                continue
            for value in dai:
                text = (value.text or "").strip()
                if strip_ns(value.tag) == "Val" and "/" in text:
                    written.append(text)
        self.assertEqual(len(written), 74)
        self.assertEqual([v for v in written if v not in produced], [])

    def test_every_written_datset_resolves_and_is_exclusive(self):
        """The definition of exclusive, checked against the file rather than
        against a fixture: every control block that names a dataset is the
        only block in its logical node that names it, in all three exports."""
        for name in ("siemens.scd", "mixed.scd"):
            doc = self.corpus(name)
            checked = 0
            for element in doc.root.iter():
                if strip_ns(element.tag) not in CONTROL_BLOCK_TAGS:
                    continue
                if not element.get("datSet"):
                    continue
                blocks = [b for b in control_blocks(
                    doc, _resolve_data_set(doc, element))]
                self.assertIn(element, blocks, f"{name}: a block lost its own dataset")
                self.assertEqual(len(blocks), 1, f"{name}: a shared dataset appeared")
                checked += 1
            self.assertGreater(checked, 40, f"{name}: the sweep shrank")

    def test_the_dataset_less_templates_are_the_majority_and_are_inert(self):
        """360 of `siemens.scd`'s 414 report control blocks carry no `datSet`.
        Removing one expands to the removal alone."""
        doc = self.corpus("siemens.scd")
        blocks = [e for e in iter_local(doc.root, "ReportControl")]
        without = [b for b in blocks if not b.get("datSet")]
        self.assertEqual((len(blocks), len(without)), (414, 360))
        self.assertEqual(len(remove_control_block(doc, Remove(without[0]))), 1)

    def test_the_edition_one_subscribers_are_children_and_go_with_the_block(self):
        """77 `IEDName` elements, every one a child of a `GSEControl`. Nothing
        has to find them: removing the block removes them."""
        doc = self.corpus("siemens.scd")
        parents = [strip_ns(doc.parent_of(e).tag)
                   for e in iter_local(doc.root, "IEDName")]
        self.assertEqual(len(parents), 77)
        self.assertEqual(set(parents), {"GSEControl"})

    def test_a_real_control_block_is_removed_and_undone_byte_for_byte(self):
        """The phase's goal, on vendor material: take a GOOSE block with
        subscribers, remove it with everything it drags along, and check each
        piece went; then undo and get the file back, byte for byte."""
        doc = self.corpus("siemens.scd")
        original = doc.to_bytes()
        block = next(b for b in iter_local(doc.root, "GSEControl")
                     if find_control_block_subscription(doc, b)
                     and b.get("datSet"))
        subscribers = find_control_block_subscription(doc, block)
        data_set = _resolve_data_set(doc, block)
        address = control_block_gse_or_smv(doc, block)
        self.assertGreater(len(subscribers), 1)
        self.assertIsNotNone(data_set)
        self.assertIsNotNone(address)

        undo = doc.apply_edit(remove_control_block(doc, Remove(block)))

        remaining = list(iter_local(doc.root, "GSEControl"))
        self.assertNotIn(block, remaining)
        self.assertIsNone(doc.parent_of(data_set))
        self.assertIsNone(doc.parent_of(address))
        for ext_ref in subscribers:
            self.assertIsNone(ext_ref.get("srcCBName"))

        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), original)

    def test_the_revision_step_continues_what_the_file_wrote(self):
        """1,014 of the corpus's 1,015 control blocks carry a `confRev`
        congruent to 1 modulo 10,000. That is not a coincidence about vendors;
        it is this rule, already applied -- up to 189 times on one block."""
        doc = self.corpus("mixed.scd")
        values, stepped = 0, 0
        for element in doc.root.iter():
            if strip_ns(element.tag) not in CONTROL_BLOCK_TAGS:
                continue
            current = element.get("confRev")
            if current is None or not current.isdigit():
                continue
            values += 1
            if int(current) % CONF_REV_STEP == 1:
                stepped += 1
            self.assertEqual(updated_conf_rev(element),
                             str(int(current) + CONF_REV_STEP))
        self.assertEqual((values, stepped), (522, 522))

    def test_the_sampled_value_blocks_find_their_addresses(self):
        """16 `SampledValueControl` against 16 `SMV`, and the corpus's only
        sampled values are here."""
        doc = self.corpus("mixed.scd")
        blocks = list(iter_local(doc.root, "SampledValueControl"))
        self.assertEqual(len(blocks), 16)
        found = [control_block_gse_or_smv(doc, b) for b in blocks]
        self.assertEqual(sum(1 for a in found if a is not None), 16)
        self.assertEqual({strip_ns(a.tag) for a in found}, {"SMV"})


def _resolve_data_set(doc, control):
    """The `DataSet` a control block names, found in its own logical node."""
    node = doc.parent_of(control)
    name = control.get("datSet")
    for data_set in iter_local(node, "DataSet"):
        if data_set.get("name") == name and doc.parent_of(data_set) is node:
            return data_set
    return None


if __name__ == "__main__":
    unittest.main()

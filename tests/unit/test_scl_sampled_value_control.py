# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""`py61850.scl.sampled_value_control` and the `SMV` it writes beside a block.

**`mixed.scd` is the working document, and it is the only candidate**: it
holds the corpus's only 16 `SampledValueControl` elements, its only 16 `SMV`
addresses, and its only four `SMVsc` declarations. `sel.scd` and `siemens.scd`
carry none of the four.

**One refusal in this phase fires on vendor material rather than on a
fixture**, which nothing in A10 managed: those four `SMVsc` declare
``max="4"`` and the IEDs carrying them hold exactly four blocks each, so they
are AT their declared limit and `can_add_sampled_value_control` refuses them
as the file stands. Every limit A10 met was far from being reached.

The `create` here is the first in the library that writes in two sections of
the file at once -- the control block in the `IED` section and the `SMV` in
`Communication` -- and the `SMV` half comes from A11's
:mod:`py61850.scl.address`, tested in `test_scl_address.py`.
"""

import tempfile
import unittest

from py61850.scl import (
    EditRejected,
    Insert,
    SclDocument,
    SetAttributes,
    can_add_sampled_value_control,
    create_sampled_value_control,
    iter_local,
    path_id,
    strip_ns,
    update_sampled_value_control,
)
from py61850.scl.supervision import _supervision_values
from tests.unit import roundtrip
from tests.unit import scl_fixtures as fx


def _named(doc, tag, name):
    found = [el for el in iter_local(doc.root, tag) if el.get("name") == name]
    assert len(found) == 1, f"{tag} {name!r}: {len(found)} found"
    return found[0]


def station(ied_services=None, blocks="", communication=None, ied_name="MU1"):
    if ied_services is None:
        ied_services = fx.services(fx.smvsc("2"))
    if communication is None:
        communication = fx.communication(
            fx.subnetwork("SN", [fx.connected_ap(ied_name, "S1")]))
    body = fx.dataset("PhsMeas", [fx.fcda("MU", "TCTR", "AmpSv", "MX")]) + blocks
    return fx.scl(
        fx.header(),
        communication,
        fx.ied(ied_name, ied_services + fx.access_point(
            "S1", fx.ldevice("MU", fx.ln0(body=body)))))


def _station_with_supervision(sv_name="SV1"):
    """`MU1` publishes a sampled-value block; `SUB` supervises it.

    An `LSVS` is the rarer half of this module's material -- `mixed.scd`
    carries all 15 in the corpus, against 585 `LGOS` -- and it is the only
    shape that exercises what `updateSampledValueControl`'s own documentation
    promises. `SUB` also carries a second `LSVS` watching something else, so
    the re-point has something it must NOT follow.
    """
    body = (fx.dataset("PhsMeas", [fx.fcda("MU", "TCTR", "AmpSv", "MX")])
            + fx.smv_control(sv_name, "PhsMeas", body=fx.smv_opts()))
    return fx.scl(
        fx.header(),
        fx.communication(fx.subnetwork("SN", [fx.connected_ap("MU1", "S1")])),
        fx.ied("MU1", fx.services(fx.smvsc("2")) + fx.access_point(
            "S1", fx.ldevice("MU", fx.ln0(body=body)))),
        fx.ied("SUB", fx.access_point("S1", fx.ldevice(
            "CFG", fx.ln0()
            + fx.supervision(ln_class="LSVS", inst="1",
                             cb_ref=f"MU1MU/LLN0.{sv_name}")
            + fx.supervision(ln_class="LSVS", inst="2",
                             cb_ref="MU1MU/LLN0.OTHER")))))


class _Base(unittest.TestCase):
    def doc(self, text=None):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        path = fx.write(self._dir.name, "a.scd", text or station())
        return SclDocument.parse(path)

    def ln0(self, doc):
        return next(iter_local(doc.root, "LN0"))

    def assertRoundTrips(self, doc, edits):
        before = doc.to_bytes()
        undo = doc.apply_edit(edits)
        self.assertNotEqual(doc.to_bytes(), before)
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)


# -- canAddSampledValueControl ----------------------------------------------

class TestCanAdd(_Base):
    def test_an_ied_that_declares_no_smvsc_is_unconstrained(self):
        doc = self.doc(station(ied_services=""))
        self.assertTrue(can_add_sampled_value_control(doc, self.ln0(doc)))

    def test_below_the_maximum_it_is_allowed(self):
        doc = self.doc()
        self.assertTrue(can_add_sampled_value_control(doc, self.ln0(doc)))

    def test_at_the_maximum_it_is_refused(self):
        doc = self.doc(station(
            ied_services=fx.services(fx.smvsc("1")),
            blocks=fx.smv_control("SV1", "PhsMeas", body=fx.smv_opts())))
        self.assertFalse(can_add_sampled_value_control(doc, self.ln0(doc)))

    def test_an_ln_is_not_a_parent_for_one(self):
        """`tLN` declares `ReportControl` and not `SampledValueControl`;
        61850-6 puts a sampled-value block on `LN0` and nowhere else."""
        doc = self.doc(station().replace("</LN0>", "</LN0>" + fx.ln("TCTR")))
        node = next(iter_local(doc.root, "LN"))
        self.assertFalse(can_add_sampled_value_control(doc, node))

    def test_the_ied_resolves_to_its_first_ln0(self):
        doc = self.doc()
        ied = next(iter_local(doc.root, "IED"))
        self.assertTrue(can_add_sampled_value_control(doc, ied))

    def test_a_foreign_namespace_element_of_the_same_name_is_not_counted(self):
        """`mixed.scd`'s `SMVApplication` privates hold 16 elements of their
        own; counting by local name would let another tool's markup refuse
        an edit. Q27's rule, applied to a second element."""
        doc = self.doc(station(
            ied_services=fx.services(fx.smvsc("1")),
            blocks=fx.private(
                "Vendor",
                '<SampledValueControl xmlns="urn:vendor" name="X"/>')))
        self.assertTrue(can_add_sampled_value_control(doc, self.ln0(doc)))


# -- createSampledValueControl ----------------------------------------------

class TestCreate(_Base):
    def test_it_returns_the_block_and_the_address(self):
        """The reference returns `Insert[]` here where its `createDataSet`
        returns one: a sampled-value stream is a control block AND a
        `Communication` entry."""
        doc = self.doc()
        edits = create_sampled_value_control(doc, self.ln0(doc), "SV1")
        self.assertEqual([type(e) for e in edits], [Insert, Insert])
        self.assertEqual([strip_ns(e.node.tag) for e in edits],
                         ["SampledValueControl", "SMV"])

    def test_an_ied_on_no_subnetwork_gets_the_block_alone(self):
        """*"and when possible `SMV`"* -- an IED with no `ConnectedAP` has
        nowhere to put an address, and that is not a refusal. It is the shape
        an engineer has before the network is designed."""
        doc = self.doc(station(communication=fx.communication()))
        edits = create_sampled_value_control(doc, self.ln0(doc), "SV1")
        self.assertEqual([strip_ns(e.node.tag) for e in edits],
                         ["SampledValueControl"])

    def test_the_smv_is_keyed_to_the_block(self):
        doc = self.doc()
        doc.apply_edit(create_sampled_value_control(doc, self.ln0(doc), "SV1"))
        address = next(iter_local(doc.root, "SMV"))
        self.assertEqual((address.get("ldInst"), address.get("cbName")),
                         ("MU", "SV1"))

    def test_the_required_smv_opts_is_always_written(self):
        doc = self.doc()
        doc.apply_edit(create_sampled_value_control(doc, self.ln0(doc), "SV1"))
        block = _named(doc, "SampledValueControl", "SV1")
        self.assertEqual([strip_ns(c.tag) for c in block], ["SmvOpts"])

    def test_the_required_attributes_are_written(self):
        """`smvID`, `smpRate` and `nofASDU` are `use="required"`, so a block
        without them is invalid whatever the caller asked for."""
        doc = self.doc()
        doc.apply_edit(create_sampled_value_control(doc, self.ln0(doc), "SV1"))
        block = _named(doc, "SampledValueControl", "SV1")
        self.assertEqual(block.get("smpRate"), "80")
        self.assertEqual(block.get("nofASDU"), "1")
        self.assertTrue(block.get("smvID"))

    def test_the_default_smv_id_is_path_id(self):
        """**Q20, answered.** The reference documents this default as
        *"IED.name/LDevice.inst/LLN0/SampledValueControl.name"*, which is what
        A9's `path_id` builds; the three path-shaped `appID` values in
        `siemens.scd` are what confirm the shape against a real file."""
        doc = self.doc()
        doc.apply_edit(create_sampled_value_control(doc, self.ln0(doc), "SV1"))
        self.assertEqual(_named(doc, "SampledValueControl", "SV1").get("smvID"),
                         path_id(doc, self.ln0(doc), "SV1"))
        self.assertEqual(_named(doc, "SampledValueControl", "SV1").get("smvID"),
                         "MU1/MU/LLN0/SV1")

    def test_a_named_smv_id_wins(self):
        doc = self.doc()
        doc.apply_edit(create_sampled_value_control(
            doc, self.ln0(doc), "SV1", smv_id="MU1_E_S1"))
        self.assertEqual(_named(doc, "SampledValueControl", "SV1").get("smvID"),
                         "MU1_E_S1")

    def test_conf_rev_starts_at_one_and_can_be_left_off(self):
        """`confRev` is `use="optional"` here, where `tReportControl` makes it
        required -- `tControlWithIEDName` is the difference."""
        doc = self.doc()
        doc.apply_edit(create_sampled_value_control(doc, self.ln0(doc), "SV1"))
        self.assertEqual(_named(doc, "SampledValueControl", "SV1").get("confRev"),
                         "1")
        doc.apply_edit(create_sampled_value_control(
            doc, self.ln0(doc), "SV2", conf_rev=None))
        self.assertIsNone(
            _named(doc, "SampledValueControl", "SV2").get("confRev"))

    def test_no_name_allocates_the_references_documented_default(self):
        """**A17b flipped half of this.** *"a unique name starting with
        `newSampledValueControl_xx` is set"* is the reference's own sentence.
        An empty name still raises."""
        doc = self.doc()
        before = [s.get("name")
                  for s in iter_local(doc.root, "SampledValueControl")]
        doc.apply_edit(create_sampled_value_control(doc, self.ln0(doc)))
        self.assertEqual(
            before + ["newSampledValueControl"],
            [s.get("name")
             for s in iter_local(doc.root, "SampledValueControl")])
        with self.assertRaisesRegex(EditRejected, "empty name"):
            create_sampled_value_control(doc, self.ln0(doc), "")

    def test_a_name_already_used_is_refused(self):
        doc = self.doc(station(
            blocks=fx.smv_control("SV1", "PhsMeas", body=fx.smv_opts())))
        with self.assertRaisesRegex(EditRejected, "already"):
            create_sampled_value_control(doc, self.ln0(doc), "SV1")

    def test_a_dat_set_naming_nothing_is_refused(self):
        doc = self.doc()
        with self.assertRaisesRegex(EditRejected, "keyref"):
            create_sampled_value_control(doc, self.ln0(doc), "SV1",
                                         dat_set="NOPE")

    def test_the_limit_refusal_names_the_declaration(self):
        doc = self.doc(station(
            ied_services=fx.services(fx.smvsc("1")),
            blocks=fx.smv_control("SV1", "PhsMeas", body=fx.smv_opts())))
        with self.assertRaisesRegex(EditRejected, "max=1"):
            create_sampled_value_control(doc, self.ln0(doc), "SV2")

    def test_force_skips_the_guard(self):
        doc = self.doc(station(
            ied_services=fx.services(fx.smvsc("1")),
            blocks=fx.smv_control("SV1", "PhsMeas", body=fx.smv_opts())))
        self.assertEqual(len(create_sampled_value_control(
            doc, self.ln0(doc), "SV2", force=True)), 2)

    def test_a_parent_that_cannot_hold_one_is_refused(self):
        doc = self.doc()
        with self.assertRaisesRegex(EditRejected, "LN0"):
            create_sampled_value_control(
                doc, next(iter_local(doc.root, "DataSet")), "SV1")

    def test_the_address_values_reach_the_smv(self):
        doc = self.doc()
        doc.apply_edit(create_sampled_value_control(
            doc, self.ln0(doc), "SV1", mac="01-0C-CD-04-00-01", app_id="4000",
            vlan_id="48C", vlan_priority="4"))
        address = next(iter_local(doc.root, "SMV"))
        self.assertEqual(
            [(p.get("type"), p.text) for p in next(iter_local(address, "Address"))],
            [("MAC-Address", "01-0C-CD-04-00-01"), ("APPID", "4000"),
             ("VLAN-ID", "48C"), ("VLAN-PRIORITY", "4")])

    def test_it_is_one_history_entry(self):
        doc = self.doc()
        self.assertRoundTrips(
            doc, create_sampled_value_control(doc, self.ln0(doc), "SV1"))


# -- updateSampledValueControl ----------------------------------------------

class TestUpdate(_Base):
    def doc(self, text=None):
        return super().doc(text or station(
            blocks=fx.smv_control("SV1", "PhsMeas", body=fx.smv_opts()),
            communication=fx.communication(fx.subnetwork("SN", [
                fx.connected_ap("MU1", "S1", fx.smv("MU", "SV1"))]))))

    def test_the_input_edit_comes_back_first(self):
        doc = self.doc()
        edit = SetAttributes(_named(doc, "SampledValueControl", "SV1"),
                             {"smpRate": "4800"})
        self.assertIs(update_sampled_value_control(doc, edit)[0], edit)

    def test_an_ordinary_attribute_adds_nothing(self):
        doc = self.doc()
        edit = SetAttributes(_named(doc, "SampledValueControl", "SV1"),
                             {"nofASDU": "2"})
        self.assertEqual(update_sampled_value_control(doc, edit), [edit])

    def test_a_rename_follows_the_smv(self):
        """The reference's first documented fan-out, found through A9's
        `control_block_gse_or_smv`."""
        doc = self.doc()
        edit = SetAttributes(_named(doc, "SampledValueControl", "SV1"),
                             {"name": "SV9"})
        edits = update_sampled_value_control(doc, edit)
        self.assertEqual([(strip_ns(e.element.tag), e.attributes)
                          for e in edits[1:]],
                         [("SMV", {"cbName": "SV9"})])

    def test_a_rename_with_no_address_changes_only_the_block(self):
        doc = self.doc(station(
            blocks=fx.smv_control("SV1", "PhsMeas", body=fx.smv_opts()),
            communication=fx.communication()))
        edit = SetAttributes(_named(doc, "SampledValueControl", "SV1"),
                             {"name": "SV9"})
        self.assertEqual(update_sampled_value_control(doc, edit), [edit])

    def test_a_dat_set_change_is_delegated_to_a9(self):
        doc = self.doc()
        edit = SetAttributes(_named(doc, "SampledValueControl", "SV1"),
                             {"datSet": "RENAMED"})
        edits = update_sampled_value_control(doc, edit)
        self.assertEqual(
            [(strip_ns(e.element.tag), e.attributes) for e in edits[1:]],
            [("DataSet", {"name": "RENAMED"})])

    def test_clearing_a_required_attribute_is_refused(self):
        doc = self.doc()
        for attribute in ("smvID", "smpRate", "nofASDU"):
            edit = SetAttributes(_named(doc, "SampledValueControl", "SV1"),
                                 {attribute: ""})
            with self.assertRaisesRegex(EditRejected, "required"):
                update_sampled_value_control(doc, edit)

    def test_a_rename_repoints_every_supervision_naming_the_block(self):
        """**The half `updateSampledValueControl`'s own documentation
        promised** -- *"also updates SMV.cbName and supervision references"* --
        which is why this function refused `False` rather than ignoring it
        while A14 was unwritten.

        The old object reference is matched EXACTLY, so the `LSVS` watching a
        different block is left alone. A13 has to resolve an IED rename
        through an index because a name is a prefix of many references and two
        IEDs can build the same one; a block rename is one whole string that
        `control_block_obj_ref` hands over before the edit applies, so that
        index is neither reused nor duplicated here."""
        doc = self.doc(_station_with_supervision())
        values = [val for val, _ in _supervision_values(doc, ("SvCBRef",))]
        self.assertEqual([(v.text or "") for v in values],
                         ["MU1MU/LLN0.SV1", "MU1MU/LLN0.OTHER"])
        edit = SetAttributes(_named(doc, "SampledValueControl", "SV1"),
                             {"name": "SV9"})
        doc.apply_edit(update_sampled_value_control(doc, edit,
                                                   ignore_supervision=False))
        self.assertEqual([(v.text or "") for v in values],
                         ["MU1MU/LLN0.SV9", "MU1MU/LLN0.OTHER"])

    def test_the_default_leaves_supervision_stale(self):
        """`True` is the default and stays it -- Q32 §9 -- so a caller that
        does not ask keeps exactly what A12 shipped. That is the cost of the
        MINOR rule and it is recorded rather than smoothed over: the `SvCBRef`
        is left naming a block that no longer answers to that name."""
        doc = self.doc(_station_with_supervision())
        values = [val for val, _ in _supervision_values(doc, ("SvCBRef",))]
        edit = SetAttributes(_named(doc, "SampledValueControl", "SV1"),
                             {"name": "SV9"})
        doc.apply_edit(update_sampled_value_control(doc, edit))
        self.assertEqual([(v.text or "") for v in values],
                         ["MU1MU/LLN0.SV1", "MU1MU/LLN0.OTHER"])

    def test_anything_but_a_rename_touches_no_supervision(self):
        doc = self.doc(_station_with_supervision())
        edit = SetAttributes(_named(doc, "SampledValueControl", "SV1"),
                             {"desc": "x"})
        self.assertEqual(
            update_sampled_value_control(doc, edit, ignore_supervision=False),
            [edit])

    def test_a_rename_with_supervision_is_one_history_entry(self):
        doc = self.doc(_station_with_supervision())
        edit = SetAttributes(_named(doc, "SampledValueControl", "SV1"),
                             {"name": "SV9"})
        self.assertRoundTrips(doc, update_sampled_value_control(
            doc, edit, ignore_supervision=False))

    def test_another_kind_of_control_block_is_refused(self):
        doc = self.doc(station(blocks=fx.gse_control("GCB1", "PhsMeas")))
        edit = SetAttributes(_named(doc, "GSEControl", "GCB1"), {"desc": "x"})
        with self.assertRaisesRegex(EditRejected, "not a SampledValueControl"):
            update_sampled_value_control(doc, edit)

    def test_it_is_one_history_entry(self):
        doc = self.doc()
        self.assertRoundTrips(doc, update_sampled_value_control(
            doc, SetAttributes(_named(doc, "SampledValueControl", "SV1"),
                               {"name": "SV9"})))


# -- the reference corpus ---------------------------------------------------

class TestCorpus(unittest.TestCase):
    def corpus(self, name="mixed.scd"):
        path = roundtrip.CORPUS / name
        if not path.is_file():
            self.skipTest("the corpus is not in this distribution")
        return SclDocument.parse(path)

    def test_the_four_smvsc_ieds_are_at_their_declared_limit(self):
        """**The only refusal in A12 that a vendor file produces.** Four
        `mixed.scd` IEDs declare ``SMVsc max="4" delivery="multicast"`` and
        hold exactly four `SampledValueControl` elements each. Nothing in A10
        got near a limit -- at most 22 datasets against a declared 50 -- so
        both of its refusals were built rather than found."""
        doc = self.corpus()
        refused = []
        for ied in iter_local(doc.root, "IED"):
            declared = [s for s in iter_local(ied, "SMVsc")]
            if not declared:
                continue
            blocks = list(iter_local(ied, "SampledValueControl"))
            refused.append((ied.get("name"), declared[0].get("max"), len(blocks)))
            for node in iter_local(ied, "LN0"):
                if list(iter_local(node, "SampledValueControl")):
                    self.assertFalse(can_add_sampled_value_control(doc, node),
                                     ied.get("name"))
        self.assertEqual([(m, n) for _name, m, n in refused],
                         [("4", 4)] * 4)

    def test_every_corpus_block_is_addressed_one_to_one(self):
        """Q21's relation, met from this side: 16 `SampledValueControl` and 16
        `SMV`, and a rename has exactly one address to follow."""
        doc = self.corpus()
        blocks = list(iter_local(doc.root, "SampledValueControl"))
        addresses = list(iter_local(doc.root, "SMV"))
        self.assertEqual((len(blocks), len(addresses)), (16, 16))
        followed = 0
        for block in blocks:
            edits = update_sampled_value_control(
                doc, SetAttributes(block, {"name": block.get("name") + "_X"}))
            followed += sum(1 for e in edits[1:]
                            if strip_ns(e.element.tag) == "SMV")
        self.assertEqual(followed, 16)

    def test_no_corpus_block_carries_an_app_id_and_all_carry_an_smv_id(self):
        """Which is why `smvID` is what the default is written for."""
        doc = self.corpus()
        blocks = list(iter_local(doc.root, "SampledValueControl"))
        self.assertEqual(sum(1 for b in blocks if b.get("smvID")), 16)
        self.assertEqual(sum(1 for b in blocks if b.get("appID")), 0)

    def test_the_three_path_shaped_app_ids_confirm_path_id(self):
        """**Q20's answer.** `path_id` was an inference A9 recorded as the one
        capability in it resting on no compared behaviour. Three of
        `siemens.scd`'s 14 `GSEControl@appID` values are path-shaped and
        `path_id` reproduces three of their four segments exactly; the first
        differs because the IED was renamed after the id was written, which
        the file's own `Header/History` still records.
        """
        doc = self.corpus("siemens.scd")
        shaped = [g for g in iter_local(doc.root, "GSEControl")
                  if "/" in (g.get("appID") or "")]
        self.assertEqual(len(shaped), 3)
        for block in shaped:
            built = path_id(doc, doc.parent_of(block), block.get("name"))
            written = block.get("appID")
            self.assertEqual(built.split("/")[1:], written.split("/")[1:])
            self.assertEqual(len(written.split("/")), 4)
            # The stale first segment is a real IED name that is no longer in
            # the file -- the rename fan-out A13 owns, left behind by DIGSI.
            self.assertNotEqual(built.split("/")[0], written.split("/")[0])
            self.assertEqual([], [e for e in iter_local(doc.root, "IED")
                                  if e.get("name") == written.split("/")[0]])


if __name__ == "__main__":
    unittest.main()

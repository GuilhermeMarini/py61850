# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Subscribing and unsubscribing an `ExtRef`.

Four different things are being asserted here, and they are not the same kind
of claim:

1. **The type tables say what the standard says.** Regenerating them from the
   IEC namespace packages must change nothing -- a real check on a machine
   that holds the distribution, skipped in CI, exactly as A7's schema check is.
2. **A subscription round-trips.** Every edit this module builds is applied
   and undone, and the document must be byte-identical to the file it was
   parsed from afterwards -- attribute order included, which is the whole
   point of `SetAttributes`' complete-description rule.
3. **The restriction checks refuse the right things**, and say which two types
   clashed when they do.
4. **The corpus agrees.** Every bound ExtRef in the reference station -- 4,320
   of them across three vendor tools -- resolves to its control block and
   passes the restriction check. A rule invented here rather than taken from
   the standard would show up as a real, working subscription being refused.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from py61850.scl import (
    Connection,
    EditRejected,
    Insert,
    Remove,
    SclDocument,
    SetAttributes,
    ext_ref_type_restrictions,
    fcda_meets_ext_ref_restrictions,
    fcda_type,
    is_subscribed,
    iter_local,
    match_data_attributes,
    match_src_attributes,
    source_control_block,
    strip_ns,
    subscribe,
    unsubscribe,
)
from py61850.scl.extref import BINDING_ATTRIBUTES
from tests.unit import roundtrip
from tests.unit import scl_fixtures as fx

REPO = Path(__file__).resolve().parents[2]
LN_PACKAGE = (REPO / "docs" / "IEC61850 files"
              / "IEC_61850-7-4.NSD.2007B5.Light.zip")


def _station():
    """A publisher with a dataset and three control blocks, a subscriber with
    three ExtRefs, and the templates that say what the published attributes
    are.

    `PUB` declares its data objects as `Ind1` and `AnIn1` -- INSTANCE names --
    while the subscriber's ExtRefs restrict on `pDO="Ind"` and `pDO="AnIn"`,
    which are the names IEC 61850-7-4 uses. That gap is the reason the type
    tables exist: nothing in this document says `Ind1` is an `SPS`.
    """
    published = fx.dataset("DS", [
        fx.fcda("LD0", "GGIO", "Ind1", "ST", ln_inst="1", da_name="stVal"),
        fx.fcda("LD0", "GGIO", "Ind1", "ST", ln_inst="1", da_name="q"),
        fx.fcda("LD0", "GGIO", "AnIn1", "MX", ln_inst="1", da_name="mag.f"),
        fx.fcda("LD0", "GGIO", "Ind1", "ST", ln_inst="1"),
        fx.fcda("LD0", "GGIO", "AmpSv1", "MX", ln_inst="1", da_name="instMag.i"),
        fx.fcda("LD0", "GGIO", "AnIn1", "MX", ln_inst="1", da_name="mag.i"),
    ])
    publisher = fx.ied("PUB", body=fx.access_point(body=fx.ldevice(
        "LD0",
        body=fx.ln0("PUB_LLN0", body=(published
                                      + fx.report_control("RCB", "DS")
                                      + fx.gse_control("GCB", "DS")
                                      + fx.smv_control("MSVCB", "DS")))
        + fx.ln("GGIO", "1", ln_type="PUB_GGIO"))))
    # The ExtRefs are written out rather than built, because their ATTRIBUTE
    # ORDER is what the round-trip assertions turn on.
    inputs = (
        "<Inputs>"
        '<ExtRef intAddr="IN1" pServT="GOOSE" pDO="Ind" pDA="stVal" desc="52a"/>'
        '<ExtRef intAddr="IN2" pServT="GOOSE" pDO="AnIn" pDA="mag.f"/>'
        '<ExtRef intAddr="IN3" pServT="SMV" pDO="AmpSv" pDA="instMag.i"/>'
        '<ExtRef pDO="Ind" pDA="stVal" desc="no internal address"/>'
        "</Inputs>"
    )
    subscriber = fx.ied("SUB", body=fx.access_point(body=fx.ldevice(
        "LD0",
        body=fx.ln0("SUB_LLN0", body=inputs)
        + fx.ln("PTRC", "1", ln_type="SUB_PTRC",
                body=fx.doi("Str") + '<Log name="L"/>'))))
    return fx.scl(
        fx.header(),
        publisher,
        subscriber,
        fx.templates(
            fx.lnode_type("PUB_LLN0", "LLN0"),
            fx.lnode_type("SUB_LLN0", "LLN0"),
            fx.lnode_type("SUB_PTRC", "PTRC"),
            fx.lnode_type("PUB_GGIO", "GGIO",
                          dos=[("Ind1", "SPS_1"), ("AnIn1", "MV_1"),
                               ("AmpSv1", "SAV_1")]),
            fx.do_type("SPS_1", "SPS", das=[
                {"name": "stVal", "bType": "BOOLEAN", "fc": "ST"},
                {"name": "q", "bType": "Quality", "fc": "ST"}]),
            fx.do_type("MV_1", "MV", das=[
                {"name": "mag", "bType": "Struct", "type": "AV_1", "fc": "MX"}]),
            fx.do_type("SAV_1", "SAV", das=[
                {"name": "instMag", "bType": "Struct", "type": "AV_1",
                 "fc": "MX"}]),
            fx.da_type("AV_1", bdas=[{"name": "f", "bType": "FLOAT32"},
                                     {"name": "i", "bType": "INT32"}]),
        ),
    )


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.doc = SclDocument.parse(
            fx.write(self._tmp.name, "station.scd", _station()))
        self.original = self.doc.to_bytes()
        self.ext_refs = list(iter_local(self.doc.root, "ExtRef"))
        self.fcdas = list(iter_local(self.doc.root, "FCDA"))
        self.blocks = {strip_ns(b.tag): b
                       for name in ("GSEControl", "ReportControl",
                                    "SampledValueControl")
                       for b in iter_local(self.doc.root, name)}

    # -- helpers ------------------------------------------------------------

    def assertUnchanged(self):
        self.assertEqual(self.doc.to_bytes(), self.original)

    def roundTrip(self, edit):
        """Apply, assert something moved, undo, assert the bytes came back."""
        undo = self.doc.apply_edit(edit)
        self.assertNotEqual(self.doc.to_bytes(), self.original)
        self.doc.apply_edit(undo)
        self.assertUnchanged()


# -- the tables -------------------------------------------------------------

class TestTheTypeTablesAreTheStandard(unittest.TestCase):
    """Generated, so the check is that they still regenerate."""

    @unittest.skipUnless(LN_PACKAGE.is_file(),
                         "the IEC namespace packages are not on this machine")
    def test_regenerating_them_changes_nothing(self):
        result = subprocess.run(
            [sys.executable, str(REPO / "tools" / "build_nsd_types.py"),
             "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_attribution_travels_with_the_table(self):
        """The licence obligation is on a GENERATED header, so it is pinned
        here the same way A7 pinned the content models'."""
        source = (REPO / "src" / "py61850" / "scl"
                  / "_nsd_types.py").read_text(encoding="utf-8")
        self.assertIn("derived from IEC 61850-7-3:2010 and IEC 61850-7-4:2020",
                      source)
        self.assertIn("NOTICE-IEC.txt", source)
        notice = (REPO / "NOTICE-IEC.txt").read_text(encoding="utf-8")
        self.assertIn("_nsd_types.py", notice)
        self.assertIn("derived from IEC 61850-7-3:2010 and IEC 61850-7-4:2020",
                      notice)


class TestTypeRestrictions(_Base):
    def restriction(self, index):
        return ext_ref_type_restrictions(self.ext_refs[index])

    def test_a_pdo_gives_the_common_data_class(self):
        self.assertEqual(self.restriction(0).cdc, "SPS")

    def test_a_pda_gives_the_basic_type(self):
        self.assertEqual(self.restriction(0).btype, "BOOLEAN")

    def test_a_constructed_attribute_is_reached_by_its_dotted_path(self):
        """`MV.mag` is an `AnalogueValue`, so `mag.f` is a `FLOAT32` -- the
        spelling an ExtRef actually uses, and the one the corpus writes."""
        self.assertEqual(self.restriction(1), ("MV", "FLOAT32"))
        self.assertEqual(self.restriction(1).btype, "FLOAT32")

    def test_an_extref_with_no_pdo_restricts_nothing(self):
        element = ET.Element("ExtRef")
        self.assertIsNone(ext_ref_type_restrictions(element))

    def test_a_pdo_the_namespace_does_not_carry_restricts_nothing(self):
        element = ET.Element("ExtRef", {"pDO": "NotADataObject"})
        self.assertIsNone(ext_ref_type_restrictions(element))

    def test_a_pda_the_class_does_not_carry_is_not_a_specification(self):
        """`SPS` has no `mag`. That is a broken restriction rather than a
        weaker one, so there is no specification to return at all."""
        element = ET.Element("ExtRef", {"pDO": "Ind", "pDA": "mag.f"})
        self.assertIsNone(ext_ref_type_restrictions(element))

    def test_a_dotted_pdo_names_a_sub_data_object(self):
        element = ET.Element("ExtRef", {"pDO": "A.phsA", "pDA": "cVal.mag.f"})
        self.assertEqual(ext_ref_type_restrictions(element),
                         ("CMV", "FLOAT32"))


class TestResolvingAnFcda(_Base):
    def test_it_resolves_through_the_publisher_templates(self):
        self.assertEqual(fcda_type(self.doc, self.fcdas[0]), ("SPS", "BOOLEAN"))

    def test_a_struct_is_walked_to_its_leaf(self):
        self.assertEqual(fcda_type(self.doc, self.fcdas[2]), ("MV", "FLOAT32"))

    def test_a_whole_data_object_has_no_basic_type(self):
        """An FCDA naming a DO and no attribute publishes all of them, and
        legally carries no `daName`."""
        self.assertEqual(fcda_type(self.doc, self.fcdas[3]), ("SPS", None))

    def test_an_fcda_outside_a_document_resolves_to_nothing(self):
        self.assertIsNone(fcda_type(self.doc, ET.Element("FCDA")))


# -- the restriction check --------------------------------------------------

class TestRestrictionsAreMet(_Base):
    def meets(self, ext_ref, fcda, **kwargs):
        return fcda_meets_ext_ref_restrictions(
            self.doc, self.ext_refs[ext_ref], self.fcdas[fcda], **kwargs)

    def test_the_matching_attribute_is_accepted(self):
        self.assertTrue(self.meets(0, 0, control_block_type="GOOSE"))

    def test_a_different_common_data_class_is_refused(self):
        """`pDO="Ind"` is an `SPS`; `AnIn1.mag.f` is an `MV`."""
        self.assertFalse(self.meets(0, 2, control_block_type="GOOSE"))

    def test_a_different_basic_type_is_refused(self):
        """Same class both sides, different attribute: `SPS.stVal` is a
        `BOOLEAN` and `SPS.q` is a `Quality`."""
        self.assertFalse(self.meets(0, 1, control_block_type="GOOSE"))

    def test_a_different_service_type_is_refused(self):
        self.assertFalse(self.meets(0, 0, control_block_type="SMV"))

    def test_no_control_block_type_does_not_check_the_service_type(self):
        """An FCDA on its own is published by nothing in particular."""
        self.assertTrue(self.meets(0, 0))

    def test_check_only_btype_ignores_the_class(self):
        """`pDO="AmpSv"` is a `SAV` and `AnIn1` an `MV`, and `instMag.i` and
        `mag.i` are both `INT32`. The default refuses it on the class; this is
        what the option is for."""
        self.assertFalse(self.meets(2, 5))
        self.assertTrue(self.meets(2, 5, check_only_btype=True))

    def test_check_only_btype_still_checks_the_basic_type(self):
        """It narrows the check; it does not switch it off. `SAV.instMag.i` is
        an `INT32` and `MV.mag.f` a `FLOAT32`."""
        self.assertFalse(self.meets(2, 2, check_only_btype=True))

    def test_check_only_btype_still_checks_the_service_type(self):
        self.assertFalse(self.meets(0, 0, control_block_type="SMV",
                                    check_only_btype=True))

    def test_a_logical_node_class_restriction_is_checked(self):
        element = ET.Element("ExtRef", {"pLN": "PTRC"})
        self.assertFalse(fcda_meets_ext_ref_restrictions(
            self.doc, element, self.fcdas[0]))
        element.set("pLN", "GGIO")
        self.assertTrue(fcda_meets_ext_ref_restrictions(
            self.doc, element, self.fcdas[0]))

    def test_check_only_btype_ignores_the_logical_node_class(self):
        element = ET.Element("ExtRef", {"pLN": "PTRC"})
        self.assertTrue(fcda_meets_ext_ref_restrictions(
            self.doc, element, self.fcdas[0], check_only_btype=True))

    def test_an_unresolvable_restriction_is_not_a_restriction(self):
        """An ExtRef that declares nothing accepts anything, and so does one
        whose `pDO` the namespace does not carry. 12,540 ExtRefs in the
        reference corpus are the first case."""
        self.assertTrue(fcda_meets_ext_ref_restrictions(
            self.doc, ET.Element("ExtRef"), self.fcdas[0]))
        self.assertTrue(fcda_meets_ext_ref_restrictions(
            self.doc, ET.Element("ExtRef", {"pDO": "Nonsense"}), self.fcdas[0]))


# -- matching ---------------------------------------------------------------

class TestMatching(_Base):
    def setUp(self):
        super().setUp()
        self.bound = self.ext_refs[0]
        self.doc.apply_edit(subscribe(self.doc, Connection(
            self.bound, self.fcdas[0], self.blocks["GSEControl"])))

    def test_a_bound_extref_matches_the_attribute_it_is_bound_to(self):
        self.assertTrue(match_data_attributes(self.doc, self.bound,
                                              self.fcdas[0]))

    def test_it_does_not_match_a_different_attribute(self):
        self.assertFalse(match_data_attributes(self.doc, self.bound,
                                               self.fcdas[1]))

    def test_the_publishers_name_is_part_of_the_match(self):
        """It is not on the FCDA -- it is the IED the FCDA sits in, which is
        why the function takes the document."""
        self.bound.set("iedName", "SOMEONE_ELSE")
        self.assertFalse(match_data_attributes(self.doc, self.bound,
                                               self.fcdas[0]))

    def test_the_source_attributes_match_the_control_block(self):
        self.assertTrue(match_src_attributes(self.doc, self.bound,
                                             self.blocks["GSEControl"]))

    def test_they_do_not_match_another_block_on_the_same_node(self):
        self.assertFalse(match_src_attributes(self.doc, self.bound,
                                              self.blocks["ReportControl"]))

    def test_an_absent_source_class_still_reads_as_lln0(self):
        """The default is applied when MATCHING, so a file that leaves the
        attribute off still resolves -- even though this library writes it."""
        del self.bound.attrib["srcLNClass"]
        self.assertTrue(match_src_attributes(self.doc, self.bound,
                                             self.blocks["GSEControl"]))

    def test_the_source_control_block_is_found(self):
        self.assertIs(source_control_block(self.doc, self.bound),
                      self.blocks["GSEControl"])

    def test_an_unbound_extref_has_no_source_control_block(self):
        self.assertIsNone(source_control_block(self.doc, self.ext_refs[1]))

    def test_a_publisher_that_is_not_in_the_file_resolves_to_nothing(self):
        self.bound.set("iedName", "ABSENT")
        self.assertIsNone(source_control_block(self.doc, self.bound))


class TestIsSubscribed(_Base):
    def setUp(self):
        super().setUp()
        self.subscriber = [ied for ied in iter_local(self.doc.root, "IED")
                           if ied.get("name") == "SUB"][0]

    def test_nothing_is_subscribed_to_begin_with(self):
        for fcda in self.fcdas:
            self.assertFalse(is_subscribed(self.doc, fcda, self.subscriber))

    def test_a_bound_member_is_reported_as_taken(self):
        self.doc.apply_edit(subscribe(self.doc, Connection(
            self.ext_refs[0], self.fcdas[0], self.blocks["GSEControl"])))
        self.assertTrue(is_subscribed(self.doc, self.fcdas[0], self.subscriber))
        self.assertFalse(is_subscribed(self.doc, self.fcdas[1], self.subscriber))

    def test_the_scope_is_respected(self):
        """The same question asked of a logical node that holds no Inputs."""
        self.doc.apply_edit(subscribe(self.doc, Connection(
            self.ext_refs[0], self.fcdas[0], self.blocks["GSEControl"])))
        ln = next(ln for ln in iter_local(self.doc.root, "LN")
                  if ln.get("lnClass") == "PTRC")
        self.assertFalse(is_subscribed(self.doc, self.fcdas[0], ln))


# -- subscribing ------------------------------------------------------------

class TestSubscribeLaterBinding(_Base):
    def connection(self, ext_ref=0, fcda=0, block="GSEControl"):
        return Connection(self.ext_refs[ext_ref], self.fcdas[fcda],
                          self.blocks[block])

    def test_it_returns_an_edit_and_applies_nothing(self):
        edit = subscribe(self.doc, self.connection())
        self.assertEqual([type(e) for e in edit], [SetAttributes])
        self.assertUnchanged()

    def test_both_halves_are_written(self):
        self.doc.apply_edit(subscribe(self.doc, self.connection()))
        bound = self.ext_refs[0]
        self.assertEqual(bound.get("iedName"), "PUB")
        self.assertEqual(bound.get("ldInst"), "LD0")
        self.assertEqual(bound.get("lnClass"), "GGIO")
        self.assertEqual(bound.get("lnInst"), "1")
        self.assertEqual(bound.get("doName"), "Ind1")
        self.assertEqual(bound.get("daName"), "stVal")
        self.assertEqual(bound.get("srcCBName"), "GCB")
        self.assertEqual(bound.get("srcLDInst"), "LD0")
        self.assertEqual(bound.get("serviceType"), "GOOSE")

    def test_what_the_extref_already_carried_is_left_alone(self):
        self.doc.apply_edit(subscribe(self.doc, self.connection()))
        self.assertEqual(self.ext_refs[0].get("intAddr"), "IN1")
        self.assertEqual(self.ext_refs[0].get("desc"), "52a")
        self.assertEqual(self.ext_refs[0].get("pDO"), "Ind")

    def test_it_round_trips(self):
        self.roundTrip(subscribe(self.doc, self.connection()))

    def test_rebinding_clears_the_previous_source(self):
        """Every binding attribute is named on every subscription, so an
        ExtRef moved from a GOOSE block to a report does not keep half of
        where it used to point."""
        self.doc.apply_edit(subscribe(self.doc, self.connection()))
        self.doc.apply_edit(subscribe(self.doc, Connection(
            self.ext_refs[0], self.fcdas[0], None)))
        self.assertIsNone(self.ext_refs[0].get("srcCBName"))
        self.assertIsNone(self.ext_refs[0].get("serviceType"))
        self.assertEqual(self.ext_refs[0].get("iedName"), "PUB")

    def test_a_report_block_is_spelled_report(self):
        """On the one ExtRef that declares no `pServT` -- the first three
        restrict themselves to GOOSE or SMV, and a report would be refused."""
        self.doc.apply_edit(subscribe(self.doc, self.connection(
            ext_ref=3, block="ReportControl")))
        self.assertEqual(self.ext_refs[3].get("serviceType"), "Report")

    def test_a_sampled_value_block_is_spelled_smv(self):
        self.doc.apply_edit(subscribe(self.doc, Connection(
            self.ext_refs[2], self.fcdas[4], self.blocks["SampledValueControl"])))
        self.assertEqual(self.ext_refs[2].get("serviceType"), "SMV")
        self.assertEqual(self.ext_refs[2].get("doName"), "AmpSv1")

    def test_the_source_class_is_written_even_when_it_is_the_default(self):
        """The schema defaults `srcLNClass` to `LLN0`, so it could be left
        off. All 4,322 bound ExtRefs in the reference corpus write it, from
        three independent vendor tools, so this library writes it too."""
        self.doc.apply_edit(subscribe(self.doc, self.connection()))
        self.assertEqual(self.ext_refs[0].get("srcLNClass"), "LLN0")

    def test_an_empty_source_prefix_is_not_written(self):
        """The other way round, and the same corpus is why: `srcPrefix` and
        `srcLNInst` appear only where they have a value."""
        self.doc.apply_edit(subscribe(self.doc, self.connection()))
        self.assertIsNone(self.ext_refs[0].get("srcPrefix"))
        self.assertIsNone(self.ext_refs[0].get("srcLNInst"))


class TestSubscribeIsRefused(_Base):
    def test_a_type_clash_is_refused_and_names_both_types(self):
        with self.assertRaises(EditRejected) as caught:
            subscribe(self.doc, Connection(self.ext_refs[0], self.fcdas[2],
                                           self.blocks["GSEControl"]))
        message = str(caught.exception)
        self.assertIn("SPS.stVal (BOOLEAN)", message)
        self.assertIn("MV.mag.f is FLOAT32", message)

    def test_a_basic_type_clash_names_both_basic_types(self):
        with self.assertRaises(EditRejected) as caught:
            subscribe(self.doc, Connection(self.ext_refs[0], self.fcdas[1],
                                           self.blocks["GSEControl"]))
        message = str(caught.exception)
        self.assertIn("BOOLEAN", message)
        self.assertIn("Quality", message)

    def test_a_service_type_clash_names_both_services(self):
        with self.assertRaises(EditRejected) as caught:
            subscribe(self.doc, Connection(
                self.ext_refs[0], self.fcdas[0],
                self.blocks["SampledValueControl"]))
        message = str(caught.exception)
        self.assertIn("GOOSE", message)
        self.assertIn("SMV", message)

    def test_a_refusal_changes_nothing(self):
        with self.assertRaises(EditRejected):
            subscribe(self.doc, Connection(self.ext_refs[0], self.fcdas[2],
                                           self.blocks["GSEControl"]))
        self.assertUnchanged()

    def test_force_skips_the_check(self):
        self.roundTrip(subscribe(self.doc, Connection(
            self.ext_refs[0], self.fcdas[2], self.blocks["GSEControl"]),
            force=True))

    def test_check_only_btype_narrows_it(self):
        """The class difference is allowed through; the basic type is not."""
        self.doc.apply_edit(subscribe(self.doc, Connection(
            self.ext_refs[2], self.fcdas[5],
            self.blocks["SampledValueControl"]), check_only_btype=True))
        self.assertEqual(self.ext_refs[2].get("doName"), "AnIn1")
        with self.assertRaises(EditRejected):
            subscribe(self.doc, Connection(
                self.ext_refs[2], self.fcdas[2],
                self.blocks["SampledValueControl"]), check_only_btype=True)

    def test_supervision_cannot_be_asked_for_yet(self):
        with self.assertRaises(EditRejected) as caught:
            subscribe(self.doc, Connection(self.ext_refs[0], self.fcdas[0],
                                           self.blocks["GSEControl"]),
                      ignore_supervision=False)
        self.assertIn("supervision", str(caught.exception))

    def test_a_control_block_that_is_not_one_is_refused(self):
        with self.assertRaises(EditRejected):
            subscribe(self.doc, Connection(self.ext_refs[0], self.fcdas[0],
                                           self.fcdas[0]))

    def test_a_sink_that_cannot_hold_an_extref_is_refused(self):
        ldevice = next(iter_local(self.doc.root, "LDevice"))
        with self.assertRaises(EditRejected):
            subscribe(self.doc, Connection(ldevice, self.fcdas[0], None))


class TestSubscribeCreatesTheExtRef(_Base):
    def logical_node(self):
        return next(ln for ln in iter_local(self.doc.root, "LN")
                    if ln.get("lnClass") == "PTRC")

    def test_a_logical_node_with_no_inputs_gains_one(self):
        node = self.logical_node()
        edit = subscribe(self.doc, Connection(node, self.fcdas[0],
                                              self.blocks["GSEControl"]))
        self.assertEqual([type(e) for e in edit], [Insert, Insert])
        self.doc.apply_edit(edit)
        inputs = list(iter_local(node, "Inputs"))
        self.assertEqual(len(inputs), 1)
        self.assertEqual(len(list(iter_local(inputs[0], "ExtRef"))), 1)

    def test_the_inputs_lands_where_the_schema_puts_it(self):
        """`tLN` is a sequence, and `Inputs` comes after `DOI` and before
        `Log`. Inserted at the end it would load here and fail in DIGSI."""
        node = self.logical_node()
        self.doc.apply_edit(subscribe(self.doc, Connection(
            node, self.fcdas[0], self.blocks["GSEControl"])))
        self.assertEqual([strip_ns(child.tag) for child in node],
                         ["DOI", "Inputs", "Log"])

    def test_two_connections_to_one_node_share_one_inputs(self):
        """The reason `subscribe` takes a list: two diffs built separately
        would each create the `Inputs` the other also creates."""
        node = self.logical_node()
        self.doc.apply_edit(subscribe(self.doc, [
            Connection(node, self.fcdas[0], self.blocks["GSEControl"]),
            Connection(node, self.fcdas[1], self.blocks["GSEControl"]),
        ]))
        self.assertEqual(len(list(iter_local(node, "Inputs"))), 1)
        self.assertEqual(len(list(iter_local(node, "ExtRef"))), 2)

    def test_an_existing_inputs_is_used(self):
        inputs = next(iter_local(self.doc.root, "Inputs"))
        edit = subscribe(self.doc, Connection(inputs, self.fcdas[0],
                                              self.blocks["GSEControl"]))
        self.assertEqual([type(e) for e in edit], [Insert])

    def test_the_created_extref_carries_the_documents_namespace(self):
        node = self.logical_node()
        self.doc.apply_edit(subscribe(self.doc, Connection(
            node, self.fcdas[0], self.blocks["GSEControl"])))
        created = next(iter_local(node, "ExtRef"))
        self.assertTrue(created.tag.startswith("{http://www.iec.ch/61850/2003/SCL}"))

    def test_it_round_trips(self):
        self.roundTrip(subscribe(self.doc, Connection(
            self.logical_node(), self.fcdas[0], self.blocks["GSEControl"])))


# -- unsubscribing ----------------------------------------------------------

class TestUnsubscribe(_Base):
    def bind(self, index=0):
        self.doc.apply_edit(subscribe(self.doc, Connection(
            self.ext_refs[index], self.fcdas[0], self.blocks["GSEControl"])))

    def test_an_internal_address_is_blanked_and_kept(self):
        self.bind()
        self.doc.apply_edit(unsubscribe(self.doc, self.ext_refs[0]))
        for name in BINDING_ATTRIBUTES:
            self.assertIsNone(self.ext_refs[0].get(name), name)
        self.assertEqual(self.ext_refs[0].get("intAddr"), "IN1")

    def test_blanking_restores_the_file_exactly(self):
        """Subscribe, unsubscribe, and the document is the file again -- which
        is only true because the inverse restores attribute ORDER."""
        self.bind()
        self.doc.apply_edit(unsubscribe(self.doc, self.ext_refs[0]))
        self.assertUnchanged()

    def test_an_extref_with_no_internal_address_is_removed(self):
        """It exists only because someone made the connection. 58 of
        `sel.scd`'s ExtRefs are this shape."""
        target = self.ext_refs[3]
        self.assertIsNone(target.get("intAddr"))
        edit = unsubscribe(self.doc, target)
        self.assertEqual([type(e) for e in edit], [Remove])
        self.doc.apply_edit(edit)
        self.assertEqual(len(list(iter_local(self.doc.root, "ExtRef"))), 3)

    def test_unsubscribing_everything_keeps_an_inputs_that_keeps_children(self):
        """Three of the four ExtRefs carry an internal address, so they are
        blanked and stay. Only the fourth leaves, and the `Inputs` is not
        empty."""
        edit = unsubscribe(self.doc, self.ext_refs)
        self.assertEqual([type(e) for e in edit].count(Remove), 1)
        self.doc.apply_edit(edit)
        self.assertEqual(len(list(iter_local(self.doc.root, "ExtRef"))), 3)
        self.assertEqual(len(list(iter_local(self.doc.root, "Inputs"))), 1)

    def test_an_emptied_inputs_goes_too(self):
        """The `Inputs` this module created, holding the one `ExtRef` it
        created: unsubscribing it leaves nothing behind at all."""
        node = next(ln for ln in iter_local(self.doc.root, "LN")
                    if ln.get("lnClass") == "PTRC")
        self.doc.apply_edit(subscribe(self.doc, Connection(
            node, self.fcdas[0], self.blocks["GSEControl"])))
        created = next(iter_local(node, "ExtRef"))
        edit = unsubscribe(self.doc, created)
        self.assertEqual([type(e) for e in edit], [Remove, Remove])
        self.doc.apply_edit(edit)
        self.assertEqual(list(iter_local(node, "Inputs")), [])

    def test_an_inputs_that_keeps_a_child_stays(self):
        """A `Private` is content someone put there; an element holding one is
        not a leaf however many ExtRefs leave."""
        inputs = next(iter_local(self.doc.root, "Inputs"))
        self.doc.apply_edit(Insert(
            inputs, ET.Element("{http://www.iec.ch/61850/2003/SCL}Private",
                               {"type": "vendor"}), inputs[0]))
        self.doc.apply_edit(unsubscribe(self.doc, self.ext_refs))
        self.assertEqual(len(list(iter_local(self.doc.root, "Inputs"))), 1)

    def test_unsubscribing_every_extref_round_trips(self):
        self.roundTrip(unsubscribe(self.doc, self.ext_refs))

    def test_an_unbound_extref_with_an_address_is_left_alone(self):
        """Nothing to take away, so no edit is produced for it."""
        self.assertEqual(unsubscribe(self.doc, self.ext_refs[1]), [])

    def test_something_that_is_not_an_extref_is_refused(self):
        with self.assertRaises(EditRejected):
            unsubscribe(self.doc, self.fcdas[0])

    def test_supervision_cannot_be_asked_for_yet(self):
        with self.assertRaises(EditRejected):
            unsubscribe(self.doc, self.ext_refs[0], ignore_supervision=False)


class TestSubscribeAndUnsubscribeAreOneEntry(_Base):
    def test_a_compound_edit_inverts_as_one(self):
        """The whole reason these return a list: `apply_edit` gives back one
        inverse, and the consumer's history has one entry to undo."""
        edit = subscribe(self.doc, [
            Connection(self.ext_refs[0], self.fcdas[0], self.blocks["GSEControl"]),
            Connection(self.ext_refs[1], self.fcdas[2], self.blocks["GSEControl"]),
        ])
        undo = self.doc.apply_edit(edit)
        self.assertEqual(self.ext_refs[0].get("srcCBName"), "GCB")
        self.assertEqual(self.ext_refs[1].get("srcCBName"), "GCB")
        self.doc.apply_edit(undo)
        self.assertUnchanged()

    def test_a_rejected_connection_leaves_the_others_unapplied(self):
        """The refusal happens while the edit is being BUILT, so there is
        nothing to roll back."""
        with self.assertRaises(EditRejected):
            subscribe(self.doc, [
                Connection(self.ext_refs[0], self.fcdas[0],
                           self.blocks["GSEControl"]),
                Connection(self.ext_refs[0], self.fcdas[2],
                           self.blocks["GSEControl"]),
            ])
        self.assertUnchanged()


# -- the corpus -------------------------------------------------------------

class TestTheCorpusAgrees(unittest.TestCase):
    """Three vendor tools, 4,320 working subscriptions, none of which has seen
    this code. A rule invented here would refuse one of them."""

    FIXTURES = ("siemens.scd", "mixed.scd", "sel.scd")

    def test_every_bound_extref_resolves_and_passes_its_restrictions(self):
        from py61850.scl.extref import SERVICE_TYPE
        seen = checked = 0
        for name in self.FIXTURES:
            path = roundtrip.CORPUS / name
            if not path.is_file():
                continue        # excluded from the sdist, like the corpus itself
            doc = SclDocument.parse(path)
            for ext_ref in iter_local(doc.root, "ExtRef"):
                if not (ext_ref.get("iedName") and ext_ref.get("srcCBName")):
                    continue
                seen += 1
                block = source_control_block(doc, ext_ref)
                self.assertIsNotNone(
                    block, f"{name}: {ext_ref.attrib} names no control block")
                if not ext_ref.get("doName"):
                    continue
                for fcda in _members(doc, ext_ref, block):
                    if not match_data_attributes(doc, ext_ref, fcda):
                        continue
                    checked += 1
                    self.assertTrue(
                        fcda_meets_ext_ref_restrictions(
                            doc, ext_ref, fcda,
                            SERVICE_TYPE[strip_ns(block.tag)]),
                        f"{name}: a working subscription was refused: "
                        f"{ext_ref.attrib} against {fcda.attrib}")
                    break
        if seen:
            self.assertGreater(seen, 4_000, "the corpus sweep shrank")
            self.assertGreater(checked, 3_000, "the corpus sweep shrank")

    def test_a_corpus_subscription_unbinds_and_rebinds(self):
        """The strongest statement available, on real material: take a bound
        ExtRef, unbind it, bind it again through this module, and every
        binding attribute must have the value the vendor tool wrote.

        **The bytes do not come back that way, and that is not a defect.** An
        attribute deleted from the middle of an element and set again lands at
        the END -- Q13 -- so a rebuilt subscription is the same subscription
        written in a different attribute order. What restores the file is the
        INVERSE, and the last two lines of the loop are that claim.

        `siemens.scd` because it is the smallest of the three -- 176 ms to
        parse -- and its 422 fully bound GOOSE ExtRefs all carry `pDO` and
        `pDA`, so the restriction check runs for real rather than abstaining.
        """
        path = roundtrip.CORPUS / "siemens.scd"
        if not path.is_file():
            self.skipTest("the corpus is not in this distribution")
        doc = SclDocument.parse(path)
        original = doc.to_bytes()
        rebuilt = 0
        for ext_ref in iter_local(doc.root, "ExtRef"):
            if not (ext_ref.get("iedName") and ext_ref.get("doName")):
                continue
            block = source_control_block(doc, ext_ref)
            fcda = next((f for f in _members(doc, ext_ref, block)
                         if match_data_attributes(doc, ext_ref, f)), None)
            if fcda is None:
                continue
            before = {name: ext_ref.get(name) for name in BINDING_ATTRIBUTES}

            undo_unsubscribe = doc.apply_edit(unsubscribe(doc, ext_ref))
            self.assertIsNone(ext_ref.get("srcCBName"))
            self.assertIsNone(ext_ref.get("doName"))

            undo_subscribe = doc.apply_edit(
                subscribe(doc, Connection(ext_ref, fcda, block)))
            self.assertEqual(
                {name: ext_ref.get(name) for name in BINDING_ATTRIBUTES},
                before,
                f"rebinding {ext_ref.get('intAddr')} did not restore it")

            doc.apply_edit(undo_subscribe)
            doc.apply_edit(undo_unsubscribe)
            self.assertEqual(doc.to_bytes(), original,
                             f"undoing {ext_ref.get('intAddr')} left the file "
                             f"different")
            rebuilt += 1
            if rebuilt == 25:
                break
        self.assertGreater(rebuilt, 20)


def _members(doc, ext_ref, block):
    """The FCDAs of the dataset ``block`` publishes."""
    if block is None:
        return []
    node = doc.parent_of(block)
    for dataset in iter_local(node, "DataSet"):
        if dataset.get("name") == block.get("datSet"):
            return list(iter_local(dataset, "FCDA"))
    return []


if __name__ == "__main__":
    unittest.main()

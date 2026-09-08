# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The IED instance model: the skeleton every data attribute hangs off."""

import tempfile
import unittest

from py61850.scl.document import SclDocument
from tests.unit import scl_fixtures as fx


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name

    def doc(self, *sections):
        return SclDocument.parse(fx.write(self.tmpdir, "t.scd", fx.scl(*sections)))


class TestIedHeaders(_Base):
    def test_the_identifying_fields(self):
        d = self.doc(fx.ied("QPC1_TR1_UPC1", type="SEL_487E", manufacturer="SEL",
                            desc="transformer protection",
                            configVersion="001", owner="Utility"))
        h = d.ied_headers["QPC1_TR1_UPC1"]
        self.assertEqual(h.type, "SEL_487E")
        self.assertEqual(h.manufacturer, "SEL")
        self.assertEqual(h.desc, "transformer protection")
        self.assertEqual(h.config_version, "001")
        self.assertEqual(h.owner, "Utility")

    def test_ied_names_is_document_order(self):
        d = self.doc(fx.ied("B"), fx.ied("A"))
        self.assertEqual(d.ied_names, ("B", "A"))

    def test_an_ied_with_no_name_is_skipped(self):
        # It cannot be referenced by a ConnectedAP or an ExtRef, so it is not
        # addressable and keying it on "" would collide with the next one.
        d = self.doc("<IED/>", fx.ied("A"))
        self.assertEqual(d.ied_names, ("A",))

    def test_headers_do_not_build_instance_trees(self):
        d = self.doc(fx.ied("A", body=fx.access_point(
            body=fx.ldevice("PRO", body=fx.ln0()))))
        d.ied_headers
        self.assertNotIn("ied:A", d._cache)


class TestIed(_Base):
    def test_access_points_and_ldevices(self):
        d = self.doc(fx.ied("A", body=fx.access_point("S1", body=(
            fx.ldevice("PRO", body=fx.ln0()) + fx.ldevice("ANN", body=fx.ln0())))))
        ied = d.ied("A")
        self.assertEqual([ap.name for ap in ied.access_points], ["S1"])
        self.assertEqual([ld.inst for ld in ied.ldevices()], ["PRO", "ANN"])

    def test_several_access_points_are_all_kept(self):
        # 41 access points across 30 IEDs in one reference SCD.
        d = self.doc(fx.ied("A", body=(
            fx.access_point("S1", body=fx.ldevice("PRO", body=fx.ln0()))
            + fx.access_point("S2", body=fx.ldevice("ANN", body=fx.ln0())))))
        self.assertEqual([ap.name for ap in d.ied("A").access_points],
                         ["S1", "S2"])
        self.assertEqual([ld.inst for ld in d.ied("A").ldevices()],
                         ["PRO", "ANN"])

    def test_an_ied_with_no_server_is_still_an_ied(self):
        # Measured: one reference station has 28 IEDs and 14 Servers. A
        # template or placeholder IED is normal and must not raise.
        d = self.doc(fx.ied("A", body=fx.access_point("S1", server=False)))
        ied = d.ied("A")
        self.assertEqual(ied.access_points[0].server, None)
        self.assertEqual(ied.ldevices(), [])

    def test_an_unknown_ied_is_none(self):
        self.assertIsNone(self.doc(fx.ied("A")).ied("NOPE"))

    def test_an_ied_is_built_once(self):
        d = self.doc(fx.ied("A", body=fx.access_point(
            body=fx.ldevice("PRO", body=fx.ln0()))))
        self.assertIs(d.ied("A"), d.ied("A"))


class TestLDevice(_Base):
    def test_ld_name_defaults_to_ied_name_plus_inst(self):
        d = self.doc(fx.ied("QPC1", body=fx.access_point(
            body=fx.ldevice("PRO", body=fx.ln0()))))
        self.assertEqual(d.ied("QPC1").ldevices()[0].ld_name, "QPC1PRO")

    def test_an_explicit_ld_name_wins(self):
        d = self.doc(fx.ied("QPC1", body=fx.access_point(
            body=fx.ldevice("PRO", ldName="BAY1PROT", body=fx.ln0()))))
        self.assertEqual(d.ied("QPC1").ldevices()[0].ld_name, "BAY1PROT")


class TestLogicalNodes(_Base):
    def test_ln0_is_named_lln0_and_flagged(self):
        d = self.doc(fx.ied("A", body=fx.access_point(
            body=fx.ldevice("PRO", body=fx.ln0()))))
        node = d.ied("A").ldevices()[0].ln0
        self.assertEqual(node.name, "LLN0")
        self.assertTrue(node.is_ln0)

    def test_an_ln_is_named_prefix_class_inst(self):
        d = self.doc(fx.ied("A", body=fx.access_point(
            body=fx.ldevice("PRO", body=fx.ln0() + fx.ln("CSWI", inst="1",
                                                         prefix="BKR1")))))
        names = [n.name for n in d.ied("A").ldevices()[0].logical_nodes]
        self.assertEqual(names, ["LLN0", "BKR1CSWI1"])

    def test_ln0_comes_first_in_logical_nodes(self):
        # Callers iterate this to build an MMS name list, where LLN0 is
        # conventionally first; and LN0 carries the datasets and control
        # blocks the rest of the LDevice refers to.
        d = self.doc(fx.ied("A", body=fx.access_point(
            body=fx.ldevice("PRO", body=fx.ln("PTRC") + fx.ln0()))))
        self.assertEqual([n.name for n in d.ied("A").ldevices()[0].logical_nodes][0],
                         "LLN0")


class TestPrivates(_Base):
    def test_privates_hang_off_every_level(self):
        # The vendor extension seam: siemenslib reads Siemens-* off these
        # instead of walking the XML a second time.
        d = self.doc(fx.ied("A", body=(
            fx.private("Siemens-IED-Id", "42")
            + fx.access_point(body=fx.ldevice(
                "PRO", body=fx.ln0(body=fx.private("Siemens-GUID", "abcd")))))))
        ied = d.ied("A")
        self.assertEqual([e.text for e in ied.privates["Siemens-IED-Id"]], ["42"])
        ln0 = ied.ldevices()[0].ln0
        self.assertEqual([e.text for e in ln0.privates["Siemens-GUID"]], ["abcd"])


_TEMPLATES = fx.templates(
    fx.lnode_type("T_PTRC", "PTRC", dos=[("Op", "DO_ACT"), ("Beh", "DO_ENS")]),
    fx.lnode_type("T_CSWI", "CSWI", dos=[("Pos", "DO_DPC")]),
    fx.do_type("DO_ACT", cdc="ACT",
               das=[{"name": "general", "fc": "ST", "bType": "BOOLEAN"},
                    {"name": "q", "fc": "ST", "bType": "Quality"},
                    {"name": "t", "fc": "ST", "bType": "Timestamp"}]),
    fx.do_type("DO_ENS", cdc="ENS",
               das=[{"name": "stVal", "fc": "ST", "bType": "Enum",
                     "type": "BehKind"}]),
    fx.do_type("DO_DPC", cdc="DPC",
               das=[{"name": "stVal", "fc": "ST", "bType": "Dbpos"},
                    {"name": "Oper", "fc": "CO", "bType": "Struct",
                     "type": "OperDPC"}]),
    fx.da_type("OperDPC", bdas=[{"name": "ctlVal", "bType": "BOOLEAN"}]),
    fx.enum_type("BehKind", values=[(1, "on"), (2, "blocked")]),
)


class _DataBase(_Base):
    def ied_with(self, ln_body="", ln_type="T_PTRC", ln_class="PTRC"):
        d = self.doc(
            fx.ied("QPC1", body=fx.access_point(body=fx.ldevice(
                "PRO", body=fx.ln0() + fx.ln(ln_class, inst="1",
                                             ln_type=ln_type, body=ln_body)))),
            _TEMPLATES)
        return d.ied("QPC1")

    def node(self, **kwargs):
        return self.ied_with(**kwargs).ldevices()[0].logical_nodes[1]


class TestDataObjects(_DataBase):
    def test_every_declared_attribute_is_present_without_any_dai(self):
        # The headline capability. The old extraction saw only attributes
        # that happened to carry a DAI override, which is not what an MMS
        # server answering GetNameList or a browser drawing a tree needs.
        op = self.node().data_objects["Op"]
        self.assertEqual(op.cdc, "ACT")
        self.assertEqual(sorted(op.attributes), ["general", "q", "t"])

    def test_the_data_object_names_come_from_the_lnode_type(self):
        self.assertEqual(sorted(self.node().data_objects), ["Beh", "Op"])

    def test_an_ln_whose_type_is_missing_has_no_data_objects(self):
        # A file may reference a type it does not carry. Report nothing;
        # do not fail the document.
        self.assertEqual(self.node(ln_type="NOPE").data_objects, {})


class TestAttributeFacts(_DataBase):
    def test_functional_constraint_and_base_type(self):
        attr = self.node().data_objects["Op"].attributes["general"]
        self.assertEqual(attr.fc, "ST")
        self.assertEqual(attr.btype, "BOOLEAN")

    def test_enum_values_are_resolved(self):
        # This is what replaces a hardcoded list of "enumerated" DO names.
        attr = self.node().data_objects["Beh"].attributes["stVal"]
        self.assertEqual(attr.btype, "Enum")
        self.assertEqual(attr.enum_values, {1: "on", 2: "blocked"})

    def test_a_struct_expands_and_inherits_the_root_fc(self):
        node = self.node(ln_type="T_CSWI", ln_class="CSWI")
        oper = node.data_objects["Pos"].attributes["Oper"]
        self.assertEqual(oper.fc, "CO")
        self.assertEqual(oper.sub_attributes["ctlVal"].fc, "CO")


class TestNames(_DataBase):
    def test_mms_item_and_reference(self):
        attr = self.node().data_objects["Op"].attributes["general"]
        self.assertEqual(attr.mms_item(), "PTRC1$ST$Op$general")
        self.assertEqual(attr.reference(), "QPC1PRO/PTRC1.Op.general")

    def test_a_nested_attribute_spells_every_level(self):
        node = self.node(ln_type="T_CSWI", ln_class="CSWI")
        ctl = node.data_objects["Pos"].attributes["Oper"].sub_attributes["ctlVal"]
        self.assertEqual(ctl.mms_item(), "CSWI1$CO$Pos$Oper$ctlVal")
        self.assertEqual(ctl.reference(), "QPC1PRO/CSWI1.Pos.Oper.ctlVal")

    def test_walk_yields_every_leaf(self):
        node = self.node(ln_type="T_CSWI", ln_class="CSWI")
        items = sorted(a.mms_item() for a in node.data_objects["Pos"].walk())
        self.assertEqual(items, ["CSWI1$CO$Pos$Oper",
                                 "CSWI1$CO$Pos$Oper$ctlVal",
                                 "CSWI1$ST$Pos$stVal"])


class TestInstanceOverlay(_DataBase):
    def test_a_dai_val_becomes_the_attribute_value(self):
        node = self.node(ln_body=fx.doi("Beh", body=fx.dai("stVal", val="1")))
        self.assertEqual(node.data_objects["Beh"].attributes["stVal"].value, "1")

    def test_an_attribute_with_no_dai_has_no_value(self):
        self.assertIsNone(
            self.node().data_objects["Beh"].attributes["stVal"].value)

    def test_s_addr_is_exposed_verbatim_and_not_interpreted(self):
        # sAddr is a standard SCL attribute whose VALUE carries a vendor
        # grammar. SEL writes "db:52A|52B?0:1:2:3" there. Reading that
        # grammar is the vendor library's job; this package hands over the
        # string it found and takes no view on it.
        node = self.node(ln_type="T_CSWI", ln_class="CSWI",
                         ln_body=fx.doi("Pos", body=fx.dai(
                             "stVal", s_addr="db:52A|52B?0:1:2:3")))
        attr = node.data_objects["Pos"].attributes["stVal"]
        self.assertEqual(attr.s_addr, "db:52A|52B?0:1:2:3")

    def test_an_sdi_reaches_a_nested_attribute(self):
        node = self.node(ln_type="T_CSWI", ln_class="CSWI",
                         ln_body=fx.doi("Pos", body=fx.sdi(
                             "Oper", body=fx.dai("ctlVal", val="true"))))
        ctl = node.data_objects["Pos"].attributes["Oper"].sub_attributes["ctlVal"]
        self.assertEqual(ctl.value, "true")

    def test_a_dai_naming_an_attribute_the_type_does_not_declare_is_ignored(self):
        # Reporting it as an attribute would invent one the device does not
        # serve. The type is the authority on what exists.
        node = self.node(ln_body=fx.doi("Op", body=fx.dai("nosuch", val="1")))
        self.assertEqual(sorted(node.data_objects["Op"].attributes),
                         ["general", "q", "t"])


class TestSubObjects(_DataBase):
    def test_sdo_becomes_a_nested_data_object(self):
        d = self.doc(
            fx.ied("Q", body=fx.access_point(body=fx.ldevice(
                "MX", body=fx.ln0() + fx.ln("MMXU", inst="1",
                                            ln_type="T_MMXU")))),
            fx.templates(
                fx.lnode_type("T_MMXU", "MMXU", dos=[("A", "DO_WYE")]),
                fx.do_type("DO_WYE", cdc="WYE", sdos=[("phsA", "DO_CMV")]),
                fx.do_type("DO_CMV", cdc="CMV",
                           das=[{"name": "q", "fc": "MX", "bType": "Quality"}]),
            ))
        node = d.ied("Q").ldevices()[0].logical_nodes[1]
        phs_a = node.data_objects["A"].sub_objects["phsA"]
        self.assertEqual(phs_a.cdc, "CMV")
        self.assertEqual(phs_a.attributes["q"].mms_item(), "MMXU1$MX$A$phsA$q")


if __name__ == "__main__":
    unittest.main()

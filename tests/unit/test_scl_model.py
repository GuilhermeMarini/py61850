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


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Datasets, control blocks and the subscriptions that point at them."""

import tempfile
import unittest

from py61850.scl.document import SclDocument
from tests.unit import scl_fixtures as fx


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name

    def ied(self, ln0_body="", ln_body="", name="QPC1"):
        text = fx.scl(fx.ied(name, body=fx.access_point(body=fx.ldevice(
            "PRO", body=(fx.ln0(body=ln0_body)
                         + fx.ln("PTRC", inst="1", body=ln_body))))))
        return SclDocument.parse(fx.write(self.tmpdir, "t.scd", text)).ied(name)

    def ln0(self, **kwargs):
        return self.ied(**kwargs).ldevices()[0].ln0


class TestDataSets(_Base):
    def test_a_dataset_and_its_members(self):
        node = self.ln0(ln0_body=fx.dataset("DSET1", fcdas=[
            fx.fcda("PRO", "PTRC", "Op", "ST", ln_inst="1", da_name="general"),
            fx.fcda("PRO", "CSWI", "Pos", "ST", ln_inst="1", prefix="BKR1",
                    da_name="stVal"),
        ]))
        ds = node.data_sets["DSET1"]
        self.assertEqual(len(ds.fcdas), 2)
        self.assertEqual(ds.fcdas[0].ln_name, "PTRC1")
        self.assertEqual(ds.fcdas[1].ln_name, "BKR1CSWI1")

    def test_an_fcda_spells_its_mms_item(self):
        node = self.ln0(ln0_body=fx.dataset("DSET1", fcdas=[
            fx.fcda("PRO", "PTRC", "Op", "ST", ln_inst="1", da_name="general")]))
        self.assertEqual(node.data_sets["DSET1"].fcdas[0].mms_item(),
                         "PTRC1$ST$Op$general")

    def test_an_fcda_naming_only_a_data_object_still_spells_an_item(self):
        # A dataset member may name a whole DO, with no daName. That is a
        # legal FCDA and its item has no trailing attribute segment.
        node = self.ln0(ln0_body=fx.dataset("DS", fcdas=[
            fx.fcda("PRO", "PTRC", "Op", "ST", ln_inst="1")]))
        self.assertEqual(node.data_sets["DS"].fcdas[0].mms_item(),
                         "PTRC1$ST$Op")

    def test_datasets_belong_to_the_ln_that_declares_them(self):
        # A DataSet under LN0 and one under an LN are both descendants of the
        # LDevice; attributing by descent would give each of them to both.
        ied = self.ied(ln0_body=fx.dataset("A"), ln_body=fx.dataset("B"))
        ld = ied.ldevices()[0]
        self.assertEqual(sorted(ld.ln0.data_sets), ["A"])
        self.assertEqual(sorted(ld.logical_nodes[1].data_sets), ["B"])


class TestControlBlocks(_Base):
    def test_a_gse_control_block(self):
        node = self.ln0(ln0_body=fx.gse_control("GCB01", "DSET1",
                                                app_id="QPC1/GCB01",
                                                conf_rev="3"))
        cb = node.control_blocks["GCB01"]
        self.assertEqual(cb.kind, "GSEControl")
        self.assertEqual(cb.dat_set, "DSET1")
        self.assertEqual(cb.app_id, "QPC1/GCB01")
        self.assertEqual(cb.conf_rev, "3")

    def test_the_key_matches_the_communication_section(self):
        # (iedName, ldInst, cbName) -- the same triple Communication keys a
        # GSE address by, which is what lets the two halves be joined.
        node = self.ln0(ln0_body=fx.gse_control("GCB01", "DSET1"))
        self.assertEqual(node.control_blocks["GCB01"].key,
                         ("QPC1", "PRO", "GCB01"))

    def test_report_and_sampled_value_blocks_are_read_too(self):
        node = self.ln0(ln0_body=(fx.report_control("BRCB1", "DSET1")
                                  + fx.smv_control("MSVCB01", "DSET2")))
        self.assertEqual(node.control_blocks["BRCB1"].kind, "ReportControl")
        self.assertEqual(node.control_blocks["MSVCB01"].kind,
                         "SampledValueControl")

    def test_the_setting_control_is_separate(self):
        # There is at most one per LN0 and it has no name, so it does not
        # belong in a mapping keyed by name.
        node = self.ln0(ln0_body=fx.setting_control(num_of_sgs="6", act_sg="2"))
        self.assertEqual(node.setting_control.num_of_sgs, "6")
        self.assertEqual(node.setting_control.act_sg, "2")
        self.assertEqual(node.control_blocks, {})

    def test_no_setting_control_gives_none(self):
        self.assertIsNone(self.ln0().setting_control)


class TestExtRefs(_Base):
    def test_a_bound_goose_subscription(self):
        node = self.ln0(ln0_body=fx.inputs(fx.ext_ref(
            iedName="PUB1", ldInst="PRO", lnClass="PTRC", lnInst="1",
            doName="Op", daName="general", serviceType="GOOSE",
            srcLDInst="PRO", srcCBName="GCB01", intAddr="VB001",
            desc="trip from bay 1")))
        ref = node.ext_refs[0]
        self.assertEqual(ref.ied_name, "PUB1")
        self.assertEqual(ref.service_type, "GOOSE")
        self.assertEqual(ref.src_cb_name, "GCB01")
        self.assertEqual(ref.int_addr, "VB001")
        self.assertEqual(ref.desc, "trip from bay 1")
        self.assertTrue(ref.is_bound)
        self.assertEqual(ref.source_key, ("PUB1", "PRO", "GCB01"))

    def test_an_unbound_extref_is_kept_and_flagged(self):
        # An ExtRef with no publisher is a template, common in an SCD
        # exported before every connection was made. It is kept rather than
        # dropped: which ones are unbound is exactly what a commissioning
        # tool is asking. `is_bound` is how a caller tells them apart.
        node = self.ln0(ln0_body=fx.inputs(fx.ext_ref(intAddr="VB002")))
        ref = node.ext_refs[0]
        self.assertFalse(ref.is_bound)
        self.assertIsNone(ref.source_key)

    def test_service_type_is_reported_verbatim_including_absent(self):
        # Measured across the reference corpus: in one SCD all 202 health
        # ExtRefs omit serviceType entirely, and in another the equivalent
        # one carries serviceType="GOOSE". Two exports of the same idea,
        # disagreeing -- so this package reports what the file says and
        # filters nothing.
        node = self.ln0(ln0_body=fx.inputs(
            fx.ext_ref(iedName="P", srcCBName="C", serviceType="GOOSE"),
            fx.ext_ref(iedName="P", srcCBName="D")))
        self.assertEqual([r.service_type for r in node.ext_refs],
                         ["GOOSE", None])

    def test_ext_refs_are_collected_across_the_whole_ied(self):
        ied = self.ied(ln0_body=fx.inputs(fx.ext_ref(iedName="P",
                                                     srcCBName="A")),
                       ln_body=fx.inputs(fx.ext_ref(iedName="P",
                                                    srcCBName="B")))
        self.assertEqual(sorted(r.src_cb_name for r in ied.ext_refs()),
                         ["A", "B"])


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The Communication section: where each IED and control block publishes."""

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


class TestSubNetworks(_Base):
    def test_subnetworks_and_their_access_points(self):
        d = self.doc(fx.communication(
            fx.subnetwork("ST1", aps=[fx.connected_ap("IED1"),
                                      fx.connected_ap("IED2")]),
            fx.subnetwork("PR1", aps=[fx.connected_ap("IED1", ap_name="S2")]),
        ))
        names = [s.name for s in d.communication.subnetworks]
        self.assertEqual(names, ["ST1", "PR1"])
        self.assertEqual([ap.ied_name for ap in
                          d.communication.subnetworks[0].connected_aps],
                         ["IED1", "IED2"])

    def test_an_ied_may_have_several_access_points(self):
        # Measured: 41 AccessPoints across 30 IEDs in one reference SCD, so
        # an IED->AP mapping that assumes one loses connections.
        d = self.doc(fx.communication(fx.subnetwork("ST1", aps=[
            fx.connected_ap("IED1", ap_name="S1"),
            fx.connected_ap("IED1", ap_name="S2"),
        ])))
        aps = [ap for ap in d.communication.connected_aps()
               if ap.ied_name == "IED1"]
        self.assertEqual([ap.ap_name for ap in aps], ["S1", "S2"])


class TestAddress(_Base):
    def test_p_elements_are_read_by_type(self):
        d = self.doc(fx.communication(fx.subnetwork("ST1", aps=[
            fx.connected_ap("IED1", body=fx.address(IP="192.0.2.60",
                                                    IP_SUBNET="255.255.255.0")),
        ])))
        ap = d.communication.connected_aps()[0]
        self.assertEqual(ap.address.ip, "192.0.2.60")
        self.assertEqual(ap.address.get("IP-SUBNET"), "255.255.255.0")

    def test_p_type_lookup_is_case_insensitive(self):
        d = self.doc(fx.communication(fx.subnetwork("ST1", aps=[
            fx.connected_ap("IED1", body=fx.address(IP="192.0.2.60")),
        ])))
        self.assertEqual(d.communication.connected_aps()[0].address.get("ip"),
                         "192.0.2.60")

    def test_ip_by_ied_takes_the_first_address(self):
        d = self.doc(fx.communication(fx.subnetwork("ST1", aps=[
            fx.connected_ap("IED1", ap_name="S1",
                            body=fx.address(IP="192.0.2.60")),
            fx.connected_ap("IED1", ap_name="S2",
                            body=fx.address(IP="192.0.2.61")),
        ])))
        self.assertEqual(d.communication.ip_by_ied(), {"IED1": "192.0.2.60"})

    def test_an_access_point_with_no_address_is_absent_from_ip_by_ied(self):
        d = self.doc(fx.communication(fx.subnetwork("ST1", aps=[
            fx.connected_ap("IED1")])))
        self.assertEqual(d.communication.ip_by_ied(), {})


class TestControlBlockAddresses(_Base):
    def test_gse_carries_mac_appid_and_vlan(self):
        d = self.doc(fx.communication(fx.subnetwork("PR1", aps=[
            fx.connected_ap("PUB1", body=fx.gse(
                "PRO", "GCB01",
                addr=fx.address(MAC_Address="01-0C-CD-01-00-01",
                                APPID="0001", VLAN_ID="005",
                                VLAN_PRIORITY="4"),
                min_time=4, max_time=1000)),
        ])))
        cbs = d.communication.control_block_addresses()
        self.assertEqual(list(cbs), [("PUB1", "PRO", "GCB01")])
        cb = cbs[("PUB1", "PRO", "GCB01")]
        self.assertEqual(cb.kind, "GSE")
        self.assertEqual(cb.address.mac, "01-0C-CD-01-00-01")
        self.assertEqual(cb.address.appid, "0001")
        self.assertEqual(cb.address.vlan_id, "005")
        self.assertEqual(cb.address.vlan_priority, "4")
        self.assertEqual(cb.min_time, "4")
        self.assertEqual(cb.max_time, "1000")

    def test_smv_blocks_are_keyed_alongside_gse(self):
        # The reference Mixed station carries 16 SMV blocks beside 97 GSE.
        d = self.doc(fx.communication(fx.subnetwork("PR1", aps=[
            fx.connected_ap("MU1", body=fx.smv("MU", "MSVCB01",
                                               addr=fx.address(APPID="4000"))),
        ])))
        cb = d.communication.control_block_addresses()[("MU1", "MU", "MSVCB01")]
        self.assertEqual(cb.kind, "SMV")
        self.assertEqual(cb.address.appid, "4000")

    def test_a_block_with_no_cb_name_is_skipped(self):
        # It identifies nothing; keeping it would put a key of ("IED","LD","")
        # in a map every caller looks up by control-block name.
        d = self.doc(fx.communication(fx.subnetwork("PR1", aps=[
            fx.connected_ap("PUB1", body=fx.gse("PRO", "")),
        ])))
        self.assertEqual(d.communication.control_block_addresses(), {})


class TestCaching(_Base):
    def test_communication_is_built_once(self):
        d = self.doc(fx.communication(fx.subnetwork("ST1")))
        self.assertIs(d.communication, d.communication)


class TestPrivateLookalike(_Base):
    def test_a_private_communication_lookalike_does_not_shadow_the_real_one(self):
        # Same class of bug commit 671bb35 fixed for <IED>: a Private is
        # free to nest a same-named element, <Communication> included.
        decoy = fx.private(
            "Vendor-Thing",
            "<Communication>" + fx.subnetwork("DECOY") + "</Communication>")
        real = fx.communication(fx.subnetwork("REAL"))
        d = self.doc(decoy, real)
        self.assertEqual([sn.name for sn in d.communication.subnetworks],
                         ["REAL"])


if __name__ == "__main__":
    unittest.main()

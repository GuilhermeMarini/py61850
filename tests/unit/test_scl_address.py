# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""`py61850.scl.address` -- the `Communication` half of a control block.

**This module is A11's and A12 started it.** `create_sampled_value_control`
has to write an `SMV` beside the block it creates, and defining that
expansion twice -- once here and once in A11 -- is the drift Q23 and Q25 were
written about, so it was defined once, where it belongs. These tests cover
only what A12 needed; A11 adds `create_gse`, the two content changes and the
address change beside them.
"""

import tempfile
import unittest

from py61850.scl import (
    EditRejected,
    Insert,
    SclDocument,
    connected_ap_for,
    create_smv,
    iter_local,
    strip_ns,
)
from tests.unit import roundtrip
from tests.unit import scl_fixtures as fx


def station(aps=None, server_ap="S1", extra_ap=""):
    if aps is None:
        aps = [fx.connected_ap("MU1", "S1")]
    return fx.scl(
        fx.header(),
        fx.communication(fx.subnetwork("SN", aps)),
        fx.ied("MU1", fx.access_point(server_ap,
                                      fx.ldevice("MU", fx.ln0())) + extra_ap))


class _Base(unittest.TestCase):
    def doc(self, text=None):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        return SclDocument.parse(fx.write(self._dir.name, "a.scd",
                                          text or station()))

    def ap(self, doc):
        return next(iter_local(doc.root, "ConnectedAP"))


# -- createSMV --------------------------------------------------------------

class TestCreateSmv(_Base):
    def test_it_returns_a_list_of_one_insert(self):
        doc = self.doc()
        edits = create_smv(doc, self.ap(doc), "MU", "SV1")
        self.assertEqual(len(edits), 1)
        self.assertIsInstance(edits[0], Insert)

    def test_an_smv_with_no_address_is_valid_and_is_what_is_written(self):
        """`tControlBlock` declares `Address` with `minOccurs="0"`, so an
        `SMV` naming only its control block is valid SCL -- and it is the
        shape that waits for A17's MAC and APPID generators rather than
        inventing them now."""
        doc = self.doc()
        doc.apply_edit(create_smv(doc, self.ap(doc), "MU", "SV1"))
        address = next(iter_local(doc.root, "SMV"))
        self.assertEqual(list(address), [])
        self.assertEqual((address.get("ldInst"), address.get("cbName")),
                         ("MU", "SV1"))

    def test_the_p_elements_are_written_in_the_references_option_order(self):
        """Nothing else fixes it: `tAddress` is a sequence over one repeated
        `P`, so the schema imposes no order, and the corpus writes three
        different ones across its 162 addresses."""
        doc = self.doc()
        doc.apply_edit(create_smv(doc, self.ap(doc), "MU", "SV1",
                                  mac="01-0C-CD-04-00-01", app_id="4000",
                                  vlan_id="48C", vlan_priority="4"))
        address = next(iter_local(next(iter_local(doc.root, "SMV")), "Address"))
        self.assertEqual([p.get("type") for p in address],
                         ["MAC-Address", "APPID", "VLAN-ID", "VLAN-PRIORITY"])

    def test_only_the_values_given_are_written(self):
        doc = self.doc()
        doc.apply_edit(create_smv(doc, self.ap(doc), "MU", "SV1",
                                  app_id="4000"))
        address = next(iter_local(next(iter_local(doc.root, "SMV")), "Address"))
        self.assertEqual([(p.get("type"), p.text) for p in address],
                         [("APPID", "4000")])

    def test_no_xsi_type_is_written(self):
        """148 corpus `P` elements carry none, and every one of `sel.scd`'s is
        among them. Writing it would need an `XMLSchema-instance` declaration
        on a root whose attribute order Q16 records as not editable."""
        doc = self.doc()
        doc.apply_edit(create_smv(doc, self.ap(doc), "MU", "SV1", app_id="4000"))
        address = next(iter_local(next(iter_local(doc.root, "SMV")), "Address"))
        self.assertEqual(list(address[0].attrib), ["type"])

    def test_a_parent_that_is_not_a_connected_ap_is_refused(self):
        doc = self.doc()
        with self.assertRaisesRegex(EditRejected, "not a ConnectedAP"):
            create_smv(doc, next(iter_local(doc.root, "LN0")), "MU", "SV1")

    def test_an_empty_key_is_refused(self):
        doc = self.doc()
        with self.assertRaisesRegex(EditRejected, "required"):
            create_smv(doc, self.ap(doc), "MU", "")

    def test_a_second_address_for_one_block_is_refused(self):
        """The pair `(ldInst, cbName)` is what identifies the address, so a
        second one is not a variant but a contradiction."""
        doc = self.doc(station(
            aps=[fx.connected_ap("MU1", "S1", fx.smv("MU", "SV1"))]))
        with self.assertRaisesRegex(EditRejected, "already"):
            create_smv(doc, self.ap(doc), "MU", "SV1")

    def test_it_is_one_history_entry(self):
        doc = self.doc()
        before = doc.to_bytes()
        undo = doc.apply_edit(create_smv(doc, self.ap(doc), "MU", "SV1"))
        self.assertNotEqual(doc.to_bytes(), before)
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)

    def test_the_new_element_carries_the_document_s_namespace(self):
        doc = self.doc()
        doc.apply_edit(create_smv(doc, self.ap(doc), "MU", "SV1"))
        self.assertEqual(next(iter_local(doc.root, "SMV")).tag,
                         self.ap(doc).tag.replace("ConnectedAP", "SMV"))

    def test_it_is_placed_by_the_content_model(self):
        """`tConnectedAP`'s sequence is Address, GSE, SMV, PhysConn -- an
        `SMV` appended after a `PhysConn` loads here and fails in DIGSI."""
        doc = self.doc(station(aps=[fx.connected_ap(
            "MU1", "S1", fx.address(**{"IP": "192.0.2.1"})
            + "<PhysConn type='RedConn'/>")]))
        doc.apply_edit(create_smv(doc, self.ap(doc), "MU", "SV1"))
        self.assertEqual([strip_ns(c.tag) for c in self.ap(doc)],
                         ["Address", "SMV", "PhysConn"])


# -- which ConnectedAP -------------------------------------------------------

class TestConnectedApFor(_Base):
    def test_it_finds_the_ied_s_access_point(self):
        doc = self.doc()
        self.assertIs(connected_ap_for(doc, "MU1"), self.ap(doc))

    def test_an_ied_on_no_subnetwork_has_none(self):
        doc = self.doc()
        self.assertIsNone(connected_ap_for(doc, "OTHER"))

    def test_a_named_access_point_wins(self):
        doc = self.doc(station(
            aps=[fx.connected_ap("MU1", "S1"), fx.connected_ap("MU1", "S2")]))
        self.assertEqual(connected_ap_for(doc, "MU1", "S2").get("apName"), "S2")

    def test_the_default_is_the_one_holding_the_server(self):
        """Not the first `ConnectedAP` in the file. The two differ on a real
        IED -- see the corpus test below."""
        doc = self.doc(station(
            aps=[fx.connected_ap("MU1", "S2"), fx.connected_ap("MU1", "S1")],
            server_ap="S1",
            extra_ap=fx.access_point("S2", server=False)))
        self.assertEqual(connected_ap_for(doc, "MU1").get("apName"), "S1")

    def test_it_falls_back_when_the_server_s_access_point_is_not_connected(self):
        """`sel.scd`'s `RTAC_1` is exactly this: a `Server` on `S1` and ten
        `ConnectedAP` elements named `Eth_01` to `Eth_10`, none of them `S1`.
        Requiring the match would mean no `SMV` could ever be written for the
        one IED in the corpus with ten connections."""
        doc = self.doc(station(aps=[fx.connected_ap("MU1", "Eth_01"),
                                    fx.connected_ap("MU1", "Eth_02")]))
        self.assertEqual(connected_ap_for(doc, "MU1").get("apName"), "Eth_01")


# -- the reference corpus ---------------------------------------------------

class TestCorpus(unittest.TestCase):
    def corpus(self, name):
        path = roundtrip.CORPUS / name
        if not path.is_file():
            self.skipTest("the corpus is not in this distribution")
        return SclDocument.parse(path)

    def test_the_rtac_is_the_case_the_fallback_exists_for(self):
        doc = self.corpus("sel.scd")
        found = connected_ap_for(doc, "RTAC_1")
        names = [ap.get("apName") for ap in iter_local(doc.root, "ConnectedAP")
                 if ap.get("iedName") == "RTAC_1"]
        self.assertEqual(len(names), 10)
        self.assertNotIn("S1", names)
        self.assertEqual(found.get("apName"), "Eth_01")

    def test_every_multi_access_point_ied_puts_its_server_in_exactly_one(self):
        """13 corpus IEDs have more than one `AccessPoint`, which is what
        makes "the one holding the `Server`" a rule that always resolves to a
        name -- even where, as on the RTAC, no `ConnectedAP` carries it."""
        counted = 0
        for name in ("sel.scd", "mixed.scd", "siemens.scd"):
            doc = self.corpus(name)
            namespace = doc.root.tag[:doc.root.tag.index("}") + 1]
            for ied in iter_local(doc.root, "IED"):
                if not ied.tag.startswith(namespace):
                    continue
                aps = [c for c in ied if strip_ns(c.tag) == "AccessPoint"]
                if len(aps) < 2:
                    continue
                counted += 1
                self.assertEqual(
                    sum(1 for ap in aps
                        if any(strip_ns(c.tag) == "Server" for c in ap)), 1,
                    ied.get("name"))
        self.assertEqual(counted, 13)

    def test_the_p_order_the_corpus_writes_is_not_one_order(self):
        """Which is why the writer follows the reference's option order
        rather than a vendor's: there is no vendor convention to follow."""
        orders = set()
        for name in ("sel.scd", "mixed.scd", "siemens.scd"):
            doc = self.corpus(name)
            for tag in ("GSE", "SMV"):
                for element in iter_local(doc.root, tag):
                    for address in iter_local(element, "Address"):
                        orders.add(tuple(p.get("type") for p in address))
        self.assertEqual(len(orders), 3)


if __name__ == "__main__":
    unittest.main()

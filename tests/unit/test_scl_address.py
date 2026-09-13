# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""`py61850.scl.address` -- the `Communication` half of a control block.

**A12 started this module and A11 finished it.** `create_sampled_value_control`
has to write an `SMV` beside the block it creates, and defining that
expansion twice -- once here and once in A11 -- is the drift Q23 and Q25 were
written about, so it was defined once, where it belongs. A12's tests are the
`createSMV` and `connected_ap_for` classes below and they are left where they
were written; A11 added `create_gse`, the shared address change, its two
wrappers, and the corpus measurement that closes Q21.
"""

import tempfile
import unittest

from py61850.scl import (
    EditRejected,
    Insert,
    SclDocument,
    SetAttributes,
    SetTextContent,
    change_gse_content,
    change_gse_or_smv_address,
    change_smv_content,
    connected_ap_for,
    create_gse,
    create_smv,
    iter_local,
    reference_for,
    strip_ns,
)
from py61850.scl import control_block_gse_or_smv
from py61850.scl.address import P_TYPES, XSI_TYPE
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
        """A12 shipped this shape and A11 keeps it, on the corpus reason
        rather than on the round-trip one A12 also gave, which was measured
        and is wrong -- see the module docstring and Q30. `inst_type=None`
        follows the `Address`, and an `Address` being created has no `P`
        elements to follow."""
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


# -- createGSE ---------------------------------------------------------------

class TestCreateGse(_Base):
    def test_it_returns_a_list_of_one_insert(self):
        doc = self.doc()
        edits = create_gse(doc, self.ap(doc), "PRO", "GCB1")
        self.assertEqual(len(edits), 1)
        self.assertIsInstance(edits[0], Insert)

    def test_a_gse_with_no_address_and_no_times_is_valid_and_is_written(self):
        """`Address`, `MinTime` and `MaxTime` are all `minOccurs="0"`. It is
        the one shape here the corpus never contains -- 146 of 146 carry all
        three -- and it is what waits for A17's generators."""
        doc = self.doc()
        doc.apply_edit(create_gse(doc, self.ap(doc), "PRO", "GCB1"))
        gse = next(iter_local(doc.root, "GSE"))
        self.assertEqual(list(gse), [])
        self.assertEqual(gse.get("ldInst"), "PRO")
        self.assertEqual(gse.get("cbName"), "GCB1")

    def test_the_times_are_written_with_their_unit_and_multiplier(self):
        """`tDurationInMilliSec` makes both optional and fixed, so they carry
        no information -- and all 146 corpus GSEs write them anyway."""
        doc = self.doc()
        doc.apply_edit(create_gse(doc, self.ap(doc), "PRO", "GCB1",
                                  min_time="4", max_time="1000"))
        gse = next(iter_local(doc.root, "GSE"))
        self.assertEqual([(strip_ns(c.tag), c.get("unit"),
                           c.get("multiplier"), c.text) for c in gse],
                         [("MinTime", "s", "m", "4"),
                          ("MaxTime", "s", "m", "1000")])

    def test_only_the_time_given_is_written(self):
        doc = self.doc()
        doc.apply_edit(create_gse(doc, self.ap(doc), "PRO", "GCB1",
                                  max_time="1000"))
        gse = next(iter_local(doc.root, "GSE"))
        self.assertEqual([strip_ns(c.tag) for c in gse], ["MaxTime"])

    def test_the_children_are_in_the_schema_s_order(self):
        """`tGSE` extends `tControlBlock`, so the sequence is `Address`,
        `MinTime`, `MaxTime` -- and the options are not given in that order
        here on purpose."""
        doc = self.doc()
        doc.apply_edit(create_gse(doc, self.ap(doc), "PRO", "GCB1",
                                  max_time="1000", min_time="4", mac="01-0C"))
        gse = next(iter_local(doc.root, "GSE"))
        self.assertEqual([strip_ns(c.tag) for c in gse],
                         ["Address", "MinTime", "MaxTime"])

    def test_the_p_elements_are_written_in_the_references_option_order(self):
        doc = self.doc()
        doc.apply_edit(create_gse(doc, self.ap(doc), "PRO", "GCB1",
                                  vlan_priority="4", app_id="3001",
                                  vlan_id="001", mac="01-0C-CD-01-00-01"))
        address = next(iter_local(next(iter_local(doc.root, "GSE")),
                                  "Address"))
        self.assertEqual([p.get("type") for p in address], list(P_TYPES))

    def test_only_the_values_given_are_written(self):
        doc = self.doc()
        doc.apply_edit(create_gse(doc, self.ap(doc), "PRO", "GCB1",
                                  app_id="3001"))
        address = next(iter_local(next(iter_local(doc.root, "GSE")),
                                  "Address"))
        self.assertEqual([p.get("type") for p in address], ["APPID"])

    def test_no_xsi_type_is_written_by_default(self):
        doc = self.doc()
        doc.apply_edit(create_gse(doc, self.ap(doc), "PRO", "GCB1",
                                  app_id="3001"))
        address = next(iter_local(next(iter_local(doc.root, "GSE")),
                                  "Address"))
        self.assertEqual(list(address[0].attrib), ["type"])

    def test_inst_type_true_writes_it_and_the_root_gains_the_declaration(self):
        """The attribute that turns the derived type's value pattern on. A
        document declaring no `xsi` prefix gains one, appended after the
        attributes the recorded layout names -- which is what `to_bytes` does
        with any attribute it did not record, and not what Q16 forbids."""
        doc = self.doc()
        doc.apply_edit(create_gse(doc, self.ap(doc), "PRO", "GCB1",
                                  app_id="3001", inst_type=True))
        address = next(iter_local(next(iter_local(doc.root, "GSE")),
                                  "Address"))
        self.assertEqual(address[0].get(XSI_TYPE), "tP_APPID")
        self.assertIn(b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
                      doc.to_bytes())

    def test_a_parent_that_is_not_a_connected_ap_is_refused(self):
        doc = self.doc()
        with self.assertRaisesRegex(EditRejected, "not a ConnectedAP"):
            create_gse(doc, next(iter_local(doc.root, "LN0")), "PRO", "GCB1")

    def test_an_empty_key_is_refused(self):
        doc = self.doc()
        with self.assertRaisesRegex(EditRejected, "required"):
            create_gse(doc, self.ap(doc), "", "GCB1")

    def test_a_second_address_for_one_block_is_refused(self):
        doc = self.doc(station(
            aps=[fx.connected_ap("MU1", "S1", fx.gse("PRO", "GCB1"))]))
        with self.assertRaisesRegex(EditRejected, "already"):
            create_gse(doc, self.ap(doc), "PRO", "GCB1")

    def test_an_smv_with_the_same_key_is_not_a_clash(self):
        """A `GSEControl` and a `SampledValueControl` are different blocks and
        the tag is part of what identifies the address."""
        doc = self.doc(station(
            aps=[fx.connected_ap("MU1", "S1", fx.smv("PRO", "GCB1"))]))
        self.assertEqual(len(create_gse(doc, self.ap(doc), "PRO", "GCB1")), 1)

    def test_it_is_one_history_entry(self):
        doc = self.doc()
        before = doc.to_bytes()
        undo = doc.apply_edit(create_gse(doc, self.ap(doc), "PRO", "GCB1",
                                         mac="01-0C-CD-01-00-01",
                                         min_time="4", max_time="1000"))
        self.assertNotEqual(doc.to_bytes(), before)
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)

    def test_the_new_element_carries_the_document_s_namespace(self):
        doc = self.doc()
        doc.apply_edit(create_gse(doc, self.ap(doc), "PRO", "GCB1",
                                  mac="01-0C", min_time="4"))
        gse = next(iter_local(doc.root, "GSE"))
        namespace = self.ap(doc).tag.replace("ConnectedAP", "")
        self.assertEqual([c.tag for c in [gse] + list(gse)],
                         [namespace + n for n in ("GSE", "Address", "MinTime")])

    def test_it_is_placed_by_the_content_model(self):
        """`tConnectedAP`'s sequence is Address, GSE, SMV, PhysConn -- a `GSE`
        appended after an `SMV` loads here and fails in DIGSI."""
        doc = self.doc(station(aps=[fx.connected_ap(
            "MU1", "S1", fx.smv("MU", "SV1") + "<PhysConn type='RedConn'/>")]))
        doc.apply_edit(create_gse(doc, self.ap(doc), "PRO", "GCB1"))
        self.assertEqual([strip_ns(c.tag) for c in self.ap(doc)],
                         ["GSE", "SMV", "PhysConn"])


# -- changeGseOrSmvAddress ---------------------------------------------------

def addressed(kind="GSE", order=("MAC_Address", "VLAN_ID", "VLAN_PRIORITY",
                                "APPID"), inst_type=False, **values):
    """A `ConnectedAP` holding one addressed `GSE` or `SMV`.

    ``order`` defaults to the arrangement 87 of the corpus's 162 addresses
    use, which is NOT the order this module writes a new one in -- that is
    the whole point of the tests below.
    """
    full = {"MAC_Address": "01-0C-CD-01-00-01", "VLAN_ID": "001",
            "VLAN_PRIORITY": "4", "APPID": "3001"}
    full.update(values)
    addr = fx.address(inst_type=inst_type, **{k: full[k] for k in order})
    body = (fx.gse("PRO", "GCB1", addr=addr, min_time="4", max_time="1000")
            if kind == "GSE" else fx.smv("MU", "SV1", addr=addr))
    return station(aps=[fx.connected_ap("MU1", "S1", body)])


class TestChangeGseOrSmvAddress(_Base):
    def block(self, doc, kind="GSE"):
        return next(iter_local(doc.root, kind))

    def address(self, doc, kind="GSE"):
        return next(iter_local(self.block(doc, kind), "Address"))

    def test_a_value_is_changed_not_replaced(self):
        """The phase's divergence, pinned. The reference's three change
        functions return `(Insert | Remove)[]` and `open-scd-core` has a
        `SetTextContent` they do not use; ours uses it."""
        doc = self.doc(addressed())
        edits = change_gse_or_smv_address(doc, self.block(doc),
                                          mac="01-0C-CD-01-00-2A")
        self.assertEqual([type(e) for e in edits], [SetTextContent])
        self.assertIs(edits[0].element, self.address(doc)[0])

    def test_the_files_p_order_survives_a_change(self):
        """What a `Remove` and an `Insert` could not do, and the reason is one
        line of A7's table: `tAddress` is one repeated `P`, so it has ONE
        slot, so `reference_for` answers "append" for every `P` there is. A
        replaced `MAC-Address` would land last."""
        doc = self.doc(addressed())
        address = self.address(doc)
        self.assertIsNone(reference_for(address, address[0].tag))
        doc.apply_edit(change_gse_or_smv_address(doc, self.block(doc),
                                                 mac="01-0C-CD-01-00-2A"))
        self.assertEqual([p.get("type") for p in self.address(doc)],
                         ["MAC-Address", "VLAN-ID", "VLAN-PRIORITY", "APPID"])
        self.assertEqual(self.address(doc)[0].text, "01-0C-CD-01-00-2A")

    def test_an_option_not_given_is_not_touched(self):
        doc = self.doc(addressed())
        before = [(p.get("type"), p.text) for p in self.address(doc)]
        doc.apply_edit(change_gse_or_smv_address(doc, self.block(doc),
                                                 app_id="4000"))
        after = [(p.get("type"), p.text) for p in self.address(doc)]
        self.assertEqual([t for t, _ in before], [t for t, _ in after])
        self.assertEqual([v for t, v in after if t != "APPID"],
                         [v for t, v in before if t != "APPID"])

    def test_nothing_is_ever_deleted(self):
        """An absent option means "leave it alone", so a `P` the call does not
        name survives -- there is no way to strip one through this function."""
        doc = self.doc(addressed())
        doc.apply_edit(change_gse_or_smv_address(doc, self.block(doc),
                                                 mac="01-0C-CD-01-00-2A"))
        self.assertEqual(len(self.address(doc)), 4)

    def test_a_value_the_element_already_carries_produces_no_edit(self):
        doc = self.doc(addressed())
        self.assertEqual(
            change_gse_or_smv_address(doc, self.block(doc), app_id="3001"), [])

    def test_a_call_that_asks_for_nothing_returns_nothing(self):
        doc = self.doc(addressed())
        self.assertEqual(change_gse_or_smv_address(doc, self.block(doc)), [])

    def test_a_type_the_address_lacks_is_inserted_and_appended(self):
        doc = self.doc(addressed(order=("MAC_Address", "APPID")))
        edits = change_gse_or_smv_address(doc, self.block(doc), vlan_id="002")
        self.assertEqual([type(e) for e in edits], [Insert])
        doc.apply_edit(edits)
        self.assertEqual([p.get("type") for p in self.address(doc)],
                         ["MAC-Address", "APPID", "VLAN-ID"])

    def test_an_element_with_no_address_gets_one(self):
        doc = self.doc(station(
            aps=[fx.connected_ap("MU1", "S1", fx.gse("PRO", "GCB1",
                                                     min_time="4"))]))
        edits = change_gse_or_smv_address(doc, self.block(doc), app_id="3001",
                                          mac="01-0C-CD-01-00-01")
        self.assertEqual([type(e) for e in edits], [Insert])
        doc.apply_edit(edits)
        self.assertEqual([strip_ns(c.tag) for c in self.block(doc)],
                         ["Address", "MinTime"])
        self.assertEqual([p.get("type") for p in self.address(doc)],
                         ["MAC-Address", "APPID"])

    def test_it_takes_an_smv_as_readily_as_a_gse(self):
        """Which is why it is public here and internal in the reference:
        `control_block_gse_or_smv` hands back either and does not say which."""
        doc = self.doc(addressed(kind="SMV"))
        doc.apply_edit(change_gse_or_smv_address(doc, self.block(doc, "SMV"),
                                                 app_id="4000"))
        self.assertEqual(self.address(doc, "SMV")[3].text, "4000")

    def test_anything_else_is_refused(self):
        doc = self.doc(addressed())
        with self.assertRaisesRegex(EditRejected, "neither a GSE nor an SMV"):
            change_gse_or_smv_address(doc, self.ap(doc), app_id="4000")

    def test_the_type_is_matched_case_insensitively(self):
        """As `Address` matches, and for its reason: an exact match would
        answer a file writing `MAC-ADDRESS` by inserting a SECOND `P` for the
        same parameter."""
        doc = self.doc(station(aps=[fx.connected_ap(
            "MU1", "S1",
            fx.gse("PRO", "GCB1",
                   addr='<Address><P type="MAC-ADDRESS">01-0C</P></Address>'))]))
        edits = change_gse_or_smv_address(doc, self.block(doc), mac="02-0D")
        self.assertEqual([type(e) for e in edits], [SetTextContent])
        doc.apply_edit(edits)
        self.assertEqual([(p.get("type"), p.text) for p in self.address(doc)],
                         [("MAC-ADDRESS", "02-0D")])

    def test_it_is_one_history_entry(self):
        doc = self.doc(addressed())
        before = doc.to_bytes()
        undo = doc.apply_edit(change_gse_or_smv_address(
            doc, self.block(doc), mac="01-0C-CD-01-00-2A", vlan_id="002",
            app_id="4000", vlan_priority="5"))
        self.assertNotEqual(doc.to_bytes(), before)
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)


class TestInstType(_Base):
    def block(self, doc):
        return next(iter_local(doc.root, "GSE"))

    def address(self, doc):
        return next(iter_local(self.block(doc), "Address"))

    def typed(self, doc):
        return [p.get(XSI_TYPE) for p in self.address(doc)]

    def test_none_follows_an_address_that_has_it(self):
        doc = self.doc(fx.scl(
            fx.header(),
            fx.communication(fx.subnetwork("SN", [fx.connected_ap(
                "MU1", "S1", fx.gse("PRO", "GCB1", addr=fx.address(
                    inst_type=True, MAC_Address="01-0C", APPID="3001")))])),
            fx.ied("MU1", fx.access_point("S1", fx.ldevice("MU", fx.ln0()))),
            xsi=True))
        doc.apply_edit(change_gse_or_smv_address(doc, self.block(doc),
                                                 vlan_id="002"))
        self.assertEqual(self.typed(doc),
                         ["tP_MAC-Address", "tP_APPID", "tP_VLAN-ID"])

    def test_none_follows_an_address_that_does_not(self):
        doc = self.doc(addressed(order=("MAC_Address",)))
        doc.apply_edit(change_gse_or_smv_address(doc, self.block(doc),
                                                 vlan_id="002"))
        self.assertEqual(self.typed(doc), [None, None])

    def test_true_sets_it_on_a_p_that_is_already_there(self):
        doc = self.doc(addressed(order=("MAC_Address",)))
        edits = change_gse_or_smv_address(doc, self.block(doc), mac="02-0D",
                                          inst_type=True)
        self.assertEqual([type(e) for e in edits],
                         [SetTextContent, SetAttributes])
        doc.apply_edit(edits)
        self.assertEqual(self.typed(doc), ["tP_MAC-Address"])

    def test_true_sets_it_even_where_the_value_is_unchanged(self):
        """The switch is about the `P` elements the call NAMES, not about the
        ones whose text happens to move."""
        doc = self.doc(addressed(order=("MAC_Address",)))
        doc.apply_edit(change_gse_or_smv_address(
            doc, self.block(doc), mac="01-0C-CD-01-00-01", inst_type=True))
        self.assertEqual(self.typed(doc), ["tP_MAC-Address"])

    def test_false_writes_none_into_an_address_that_has_them(self):
        doc = self.doc(fx.scl(
            fx.header(),
            fx.communication(fx.subnetwork("SN", [fx.connected_ap(
                "MU1", "S1", fx.gse("PRO", "GCB1", addr=fx.address(
                    inst_type=True, MAC_Address="01-0C")))])),
            fx.ied("MU1", fx.access_point("S1", fx.ldevice("MU", fx.ln0()))),
            xsi=True))
        doc.apply_edit(change_gse_or_smv_address(doc, self.block(doc),
                                                 app_id="3001",
                                                 inst_type=False))
        self.assertEqual(self.typed(doc), ["tP_MAC-Address", None])

    def test_false_takes_none_away_either(self):
        """An option that is not asked about is never taken away -- the same
        rule the absent values follow."""
        doc = self.doc(fx.scl(
            fx.header(),
            fx.communication(fx.subnetwork("SN", [fx.connected_ap(
                "MU1", "S1", fx.gse("PRO", "GCB1", addr=fx.address(
                    inst_type=True, MAC_Address="01-0C")))])),
            fx.ied("MU1", fx.access_point("S1", fx.ldevice("MU", fx.ln0()))),
            xsi=True))
        doc.apply_edit(change_gse_or_smv_address(doc, self.block(doc),
                                                 mac="02-0D", inst_type=False))
        self.assertEqual(self.typed(doc), ["tP_MAC-Address"])

    def test_the_five_functions_agree(self):
        """One rule, on every function in the module, which is one option more
        than the reference gives its creates -- and the reason is that two
        functions disagreeing about `xsi:type` is the drift Q23 was about."""
        doc = self.doc()
        for edits in (create_gse(doc, self.ap(doc), "PRO", "G1",
                                 app_id="3001", inst_type=True),
                      create_smv(doc, self.ap(doc), "MU", "S1",
                                 app_id="3002", inst_type=True)):
            doc.apply_edit(edits)
        written = [p.get(XSI_TYPE)
                   for tag in ("GSE", "SMV")
                   for p in next(iter_local(next(iter_local(doc.root, tag)),
                                            "Address"))]
        self.assertEqual(written, ["tP_APPID", "tP_APPID"])

    def test_setting_it_is_invertible_bytes_and_declaration_alike(self):
        doc = self.doc(addressed(order=("MAC_Address",)))
        before = doc.to_bytes()
        undo = doc.apply_edit(change_gse_or_smv_address(
            doc, self.block(doc), mac="02-0D", inst_type=True))
        self.assertIn(b"xmlns:xsi", doc.to_bytes())
        doc.apply_edit(undo)
        self.assertNotIn(b"xmlns:xsi", doc.to_bytes())
        self.assertEqual(doc.to_bytes(), before)


# -- changeGSEContent and changeSMVContent -----------------------------------

class TestChangeGseContent(_Base):
    def gse(self, doc):
        return next(iter_local(doc.root, "GSE"))

    def times(self, doc):
        return [(strip_ns(c.tag), c.get("unit"), c.get("multiplier"), c.text)
                for c in self.gse(doc) if strip_ns(c.tag) != "Address"]

    def test_it_sets_the_text_of_a_time_that_is_there(self):
        doc = self.doc(addressed())
        edits = change_gse_content(doc, self.gse(doc), max_time="2000")
        self.assertEqual([type(e) for e in edits], [SetTextContent])
        doc.apply_edit(edits)
        self.assertEqual(self.times(doc),
                         [("MinTime", "s", "m", "4"),
                          ("MaxTime", "s", "m", "2000")])

    def test_a_time_that_is_missing_is_inserted_in_the_schema_s_order(self):
        """A `MinTime` appended after a `MaxTime` loads here and fails in
        DIGSI."""
        doc = self.doc(station(aps=[fx.connected_ap(
            "MU1", "S1", fx.gse("PRO", "GCB1", max_time="1000"))]))
        doc.apply_edit(change_gse_content(doc, self.gse(doc), min_time="4"))
        self.assertEqual(self.times(doc),
                         [("MinTime", "s", "m", "4"),
                          ("MaxTime", "s", "m", "1000")])

    def test_a_time_that_is_there_keeps_the_attributes_it_has(self):
        """Only a created element is given `unit` and `multiplier`; a change
        sets the text and nothing else."""
        doc = self.doc(station(aps=[fx.connected_ap(
            "MU1", "S1", "<GSE ldInst='PRO' cbName='GCB1'>"
                         "<MinTime>4</MinTime></GSE>")]))
        doc.apply_edit(change_gse_content(doc, self.gse(doc), min_time="10"))
        self.assertEqual(self.times(doc), [("MinTime", None, None, "10")])

    def test_a_time_not_given_is_left_alone(self):
        doc = self.doc(addressed())
        doc.apply_edit(change_gse_content(doc, self.gse(doc), min_time="10"))
        self.assertEqual(self.times(doc),
                         [("MinTime", "s", "m", "10"),
                          ("MaxTime", "s", "m", "1000")])

    def test_it_changes_the_address_half_too(self):
        doc = self.doc(addressed())
        doc.apply_edit(change_gse_content(doc, self.gse(doc), app_id="4000",
                                          max_time="2000"))
        address = next(iter_local(self.gse(doc), "Address"))
        self.assertEqual([p.text for p in address if p.get("type") == "APPID"],
                         ["4000"])
        self.assertEqual(self.times(doc)[1], ("MaxTime", "s", "m", "2000"))

    def test_a_no_op_returns_nothing(self):
        doc = self.doc(addressed())
        self.assertEqual(
            change_gse_content(doc, self.gse(doc), min_time="4",
                               app_id="3001"), [])

    def test_an_smv_is_refused(self):
        """`MinTime` and `MaxTime` are `tGSE`'s; `tSMV` extends
        `tControlBlock` and adds nothing."""
        doc = self.doc(addressed(kind="SMV"))
        with self.assertRaisesRegex(EditRejected, "not a GSE"):
            change_gse_content(doc, next(iter_local(doc.root, "SMV")),
                               min_time="4")

    def test_it_is_one_history_entry(self):
        doc = self.doc(addressed())
        before = doc.to_bytes()
        undo = doc.apply_edit(change_gse_content(
            doc, self.gse(doc), mac="02-0D", min_time="10", max_time="2000"))
        self.assertNotEqual(doc.to_bytes(), before)
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)


class TestChangeSmvContent(_Base):
    def test_it_changes_the_address(self):
        doc = self.doc(addressed(kind="SMV"))
        smv = next(iter_local(doc.root, "SMV"))
        doc.apply_edit(change_smv_content(doc, smv, app_id="4000"))
        address = next(iter_local(smv, "Address"))
        self.assertEqual([p.text for p in address if p.get("type") == "APPID"],
                         ["4000"])

    def test_a_gse_is_refused(self):
        """It is not an alias of the engine: each wrapper refuses the other's
        tag, and a caller holding an element whose tag it has not inspected
        wants `change_gse_or_smv_address`."""
        doc = self.doc(addressed())
        with self.assertRaisesRegex(EditRejected, "not an SMV"):
            change_smv_content(doc, next(iter_local(doc.root, "GSE")),
                               app_id="4000")

    def test_it_is_one_history_entry(self):
        doc = self.doc(addressed(kind="SMV"))
        before = doc.to_bytes()
        undo = doc.apply_edit(change_smv_content(
            doc, next(iter_local(doc.root, "SMV")), mac="02-0D"))
        self.assertNotEqual(doc.to_bytes(), before)
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)


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

    def test_every_corpus_block_is_addressed_one_to_one_from_this_end(self):
        """**Q21, closed.** A9 removed a control block's `GSE`/`SMV` with it,
        on the ground that the two are 1:1 in every corpus file, and said A11
        would meet the same relation from the other end and revisit the
        decision if it did not hold.

        It holds exactly. 162 `GSEControl` and `SampledValueControl` elements
        across the three files, 162 of them addressed through A9's own
        `control_block_gse_or_smv`, 162 `GSE` and `SMV` elements, and not one
        of those names a control block that is not in the file. There is
        nothing to revisit and nothing about `remove_control_block` changes.
        """
        blocks = addresses = addressed = orphans = 0
        for name in ("sel.scd", "mixed.scd", "siemens.scd"):
            doc = self.corpus(name)
            namespace = doc.root.tag[:doc.root.tag.index("}") + 1]
            names = set()
            for element in doc.root.iter():
                if element.tag in (namespace + "GSEControl",
                                   namespace + "SampledValueControl"):
                    blocks += 1
                    names.add(element.get("name"))
                    if control_block_gse_or_smv(doc, element) is not None:
                        addressed += 1
            for element in doc.root.iter():
                if element.tag in (namespace + "GSE", namespace + "SMV"):
                    addresses += 1
                    if element.get("cbName") not in names:
                        orphans += 1
        self.assertEqual((blocks, addressed, addresses, orphans),
                         (162, 162, 162, 0))

    def test_every_corpus_address_is_complete_and_uniform(self):
        """Which is why `create_gse` writing a `GSE` with no `Address` and no
        times is the one shape here the corpus never contains: all 146 carry
        `Address`, `MinTime`, `MaxTime` in that order, all 16 `SMV` carry an
        `Address` alone, and every one of the 162 carries all four `P` types
        even though `tControlBlock` makes `Address` optional."""
        shapes, times = set(), set()
        for name in ("sel.scd", "mixed.scd", "siemens.scd"):
            doc = self.corpus(name)
            namespace = doc.root.tag[:doc.root.tag.index("}") + 1]
            for element in doc.root.iter():
                if element.tag not in (namespace + "GSE", namespace + "SMV"):
                    continue
                shapes.add((strip_ns(element.tag),
                            tuple(strip_ns(c.tag) for c in element)))
                for child in element:
                    if strip_ns(child.tag) == "Address":
                        self.assertEqual(
                            sorted(p.get("type") for p in child),
                            sorted(P_TYPES), name)
                    else:
                        times.add((child.get("unit"), child.get("multiplier")))
        self.assertEqual(shapes, {
            ("GSE", ("Address", "MinTime", "MaxTime")), ("SMV", ("Address",))})
        self.assertEqual(times, {("s", "m")})

    def test_xsi_type_is_a_per_vendor_habit_not_a_per_file_one(self):
        """The evidence behind `inst_type=None` following the `Address`.
        Every address is uniform -- never partly typed -- and the eight
        untyped `P` elements in the two files that otherwise write it belong
        to FOUR `GSE` elements, all four on the SEL relays inside those
        Siemens exports. The tool that wrote the IED decided, not the tool
        that wrote the file, and one file carries both conventions."""
        counts, untyped = {}, {}
        for name in ("sel.scd", "mixed.scd", "siemens.scd"):
            doc = self.corpus(name)
            namespace = doc.root.tag[:doc.root.tag.index("}") + 1]
            typed = total = 0
            for ap in doc.root.iter():
                if ap.tag != namespace + "ConnectedAP":
                    continue
                for element in ap:
                    if strip_ns(element.tag) not in ("GSE", "SMV"):
                        continue
                    for address in iter_local(element, "Address"):
                        flags = {XSI_TYPE in p.attrib for p in address}
                        self.assertEqual(len(flags), 1, "address not uniform")
                        total += len(address)
                        if flags == {True}:
                            typed += len(address)
                        else:
                            untyped.setdefault(name, set()).add(
                                ap.get("iedName"))
            counts[name] = (typed, total)
        self.assertEqual(counts, {"sel.scd": (0, 140),
                                  "mixed.scd": (444, 452),
                                  "siemens.scd": (48, 56)})
        # sel.scd writes none anywhere, so only the two files that DO write it
        # say anything about whose habit it is -- and in both, the exceptions
        # are the SEL relays inside a Siemens export.
        self.assertEqual(untyped["mixed.scd"], {"TR01_2414", "TR01_2440"})
        self.assertEqual(untyped["siemens.scd"], {"TR1_2414", "TR1_2440"})

    def test_every_corpus_value_already_matches_its_derived_type(self):
        """So turning `inst_type` on cannot make a corpus file invalid. That
        is not free information: `tP` itself has no pattern, and these four
        are reachable only through `xsi:type`."""
        patterns = {
            "MAC-Address": r"[0-9A-F]{2}(-[0-9A-F]{2}){5}\Z",
            "APPID": r"[0-9A-F]{4}\Z",
            "VLAN-ID": r"[0-9A-F]{3}\Z",
            "VLAN-PRIORITY": r"[0-7]\Z",
        }
        checked = 0
        for name in ("sel.scd", "mixed.scd", "siemens.scd"):
            doc = self.corpus(name)
            namespace = doc.root.tag[:doc.root.tag.index("}") + 1]
            for element in doc.root.iter():
                if element.tag not in (namespace + "GSE", namespace + "SMV"):
                    continue
                for address in iter_local(element, "Address"):
                    for p in address:
                        checked += 1
                        self.assertRegex((p.text or "").strip(),
                                         patterns[p.get("type")], name)
        self.assertEqual(checked, 648)

    def test_a_goose_address_is_created_and_changed_on_a_corpus_file(self):
        """The phase's goal, on a real 22 MB export: create a `GSE`, change
        it, and put the file back exactly. `sel.scd` is the awkward one --
        it writes its `P` elements `MAC-Address, APPID, VLAN-PRIORITY,
        VLAN-ID` and declares no `xsi` prefix at all."""
        doc = self.corpus("sel.scd")
        before = doc.to_bytes()
        ap = connected_ap_for(doc, "RTAC_1")
        undo = doc.apply_edit(create_gse(
            doc, ap, "CFG", "GoNew01", mac="01-0C-CD-01-00-FF", app_id="3FFF",
            vlan_id="001", vlan_priority="4", min_time="4", max_time="1000"))
        created = next(element for element in ap
                       if element.get("cbName") == "GoNew01")
        self.assertEqual([strip_ns(c.tag) for c in created],
                         ["Address", "MinTime", "MaxTime"])

        existing = next(iter_local(doc.root, "GSE"))
        order = [p.get("type") for p in next(iter_local(existing, "Address"))]
        second = doc.apply_edit(change_gse_content(
            doc, existing, mac="01-0C-CD-01-00-2A", max_time="2000"))
        self.assertEqual(
            [p.get("type") for p in next(iter_local(existing, "Address"))],
            order)
        self.assertEqual(next(iter_local(existing, "Address"))[0].text,
                         "01-0C-CD-01-00-2A")

        doc.apply_edit(second)
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)

    def test_a_sampled_value_address_is_changed_on_a_corpus_file(self):
        """`mixed.scd` is the only corpus file with a `SampledValueControl`,
        and it writes `xsi:type` -- so a `P` inserted here follows it without
        the caller saying so."""
        doc = self.corpus("mixed.scd")
        before = doc.to_bytes()
        block = next(element for element in doc.root.iter()
                     if strip_ns(element.tag) == "SampledValueControl")
        smv = control_block_gse_or_smv(doc, block)
        self.assertEqual(strip_ns(smv.tag), "SMV")

        address = next(iter_local(smv, "Address"))
        appid = next(p for p in address if p.get("type") == "APPID")
        undo = doc.apply_edit(change_smv_content(doc, smv, app_id="4FFF",
                                                 vlan_priority="7"))
        self.assertEqual(appid.text, "4FFF")
        # Setting the TEXT leaves the attributes alone, so the file's own
        # `xsi:type` survives a change that never mentions it -- which is
        # what a `Remove` and an `Insert` would have had to reconstruct.
        self.assertEqual(appid.get(XSI_TYPE), "tP_APPID")
        # And the file's own `P` order survives -- which here is the third of
        # the three the corpus writes, neither this module's option order nor
        # the one sel.scd uses.
        self.assertEqual([p.get("type") for p in address],
                         ["VLAN-ID", "VLAN-PRIORITY", "MAC-Address", "APPID"])

        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)


if __name__ == "__main__":
    unittest.main()

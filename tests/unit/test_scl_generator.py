# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""`py61850.scl.generator` -- allocating a value nothing else has taken.

**This module has a real corpus behind it, unlike A16 and A16b**, and
`TestCorpus` is where the decisions were actually taken: 162 addresses across
three vendor exports settle the MAC prefixes and the APPID bands outright,
and 13,932 `LN@inst` settle what an instance number looks like in practice.
That is why there is a `TestCorpus` here and none in `test_scl_substation.py`.

**The fixtures vary by DATA, not by structure** -- one document, different
values already in it -- which is the condition Q37 §8 named for a single
builder paying for itself. :func:`station` is that builder.
"""

import re
import tempfile
import unittest
from xml.etree import ElementTree as ET

from py61850.scl import (
    ALLOCATED_NAME_PREFIX,
    APP_ID_RANGES,
    LN_INST_ELEMENTS,
    LN_INST_RANGE,
    MAC_ADDRESS_PREFIXES,
    SERVICE_TYPES,
    EditRejected,
    SclDocument,
    next_app_id,
    next_ln_inst,
    next_mac_address,
    strip_ns,
    unique_element_name,
)
from tests.unit import roundtrip
from tests.unit import scl_fixtures as fx


def station(gse_macs=("01-0C-CD-01-00-00",), smv_macs=(),
            gse_app_ids=("0000",), smv_app_ids=(), extra=""):
    """One document whose taken values are the parameters.

    Everything about the shape is fixed; only what is already allocated
    changes, which is what lets 40-odd tests read one builder.
    """
    def block(builder, prefix, macs, app_ids):
        out = []
        for index in range(max(len(macs), len(app_ids))):
            params = {}
            if index < len(macs):
                params["MAC_Address"] = macs[index]
            if index < len(app_ids):
                params["APPID"] = app_ids[index]
            out.append(fx.connected_ap(
                f"{prefix}{index}",
                body=builder("LD0", f"{prefix}CB{index}",
                             addr=fx.address(**params))))
        return out

    aps = (block(fx.gse, "IED", list(gse_macs), list(gse_app_ids))
           + block(fx.smv, "MU", list(smv_macs), list(smv_app_ids)))
    return fx.scl(fx.header(),
                  fx.communication(fx.subnetwork("SN1", aps=aps)), extra)


class _Base(unittest.TestCase):
    def doc(self, text=None, name="a.scd"):
        if not hasattr(self, "_dir"):
            self._dir = tempfile.TemporaryDirectory()
            self.addCleanup(self._dir.cleanup)
        return SclDocument.parse(fx.write(self._dir.name, name,
                                          text if text is not None
                                          else station()))

    def element(self, text):
        return ET.fromstring(text)


# -- the tables, pinned as derivations --------------------------------------

class TestDerivedTables(unittest.TestCase):
    """Each table is a reading of something -- the schema, IEC 61850-8-1
    through the reference, or the corpus -- and the test pins the derivation
    rather than the convenience."""

    def test_the_two_service_types_are_the_references_spelling(self):
        self.assertEqual(("GSE", "SMV"), SERVICE_TYPES)

    def test_the_mac_prefixes_are_the_two_the_corpus_measures(self):
        self.assertEqual({"GSE": "01-0C-CD-01", "SMV": "01-0C-CD-04"},
                         MAC_ADDRESS_PREFIXES)

    def test_the_appid_bands_do_not_overlap_and_smv_has_no_type_1a(self):
        self.assertEqual({("GSE", False): (0x0000, 0x3FFF),
                          ("GSE", True): (0x8000, 0xBFFF),
                          ("SMV", False): (0x4000, 0x7FFF)}, APP_ID_RANGES)
        spans = [range(low, high + 1) for low, high in APP_ID_RANGES.values()]
        for first in range(len(spans)):
            for second in range(first + 1, len(spans)):
                self.assertEqual(
                    set(), set(spans[first]) & set(spans[second]),
                    "two APPID bands overlap; a value would be ambiguous")

    def test_ln_inst_range_is_the_references_convention_not_the_schemas(self):
        # `tLNInst` is `[0-9]{1,12}` in 2007B4 and 2007C5 and `value="99"` is
        # in neither. The bound is kept and the attribution corrected -- Q35
        # §9 scheduled exactly this.
        self.assertEqual((1, 99), LN_INST_RANGE)

    def test_the_two_instance_attributes_are_spelled_differently(self):
        # This pair is why the reference documents its generator as returning
        # "`inst` or `lnInst`". LN0 is in neither, having no instance number.
        self.assertEqual({"LN": "inst", "LNode": "lnInst"}, LN_INST_ELEMENTS)
        self.assertNotIn("LN0", LN_INST_ELEMENTS)

    def test_ln_inst_range_is_still_reachable_from_its_old_home(self):
        # It moved to `generator.py` and `supervision.py` re-exports it. The
        # value and every allocation made from it are unchanged, which is what
        # keeps this a MINOR change under A18's rule.
        from py61850.scl import supervision
        from py61850.scl import generator
        self.assertIs(supervision.LN_INST_RANGE, generator.LN_INST_RANGE)


# -- MAC addresses ----------------------------------------------------------

class TestMacAddress(_Base):
    def test_the_lowest_free_address_is_returned(self):
        doc = self.doc(station(gse_macs=("01-0C-CD-01-00-00",)), "mac_a.scd")
        self.assertEqual("01-0C-CD-01-00-01", next_mac_address(doc, "GSE"))

    def test_a_hole_is_filled_before_the_end_is_extended(self):
        # 26 of the corpus's 59 (IED, class) blocks have a hole in them, and
        # appending would leave every one of them forever.
        doc = self.doc(station(gse_macs=("01-0C-CD-01-00-00",
                                         "01-0C-CD-01-00-02")), "mac_b.scd")
        self.assertEqual("01-0C-CD-01-00-01", next_mac_address(doc, "GSE"))

    def test_the_octet_boundary_carries(self):
        macs = tuple(f"01-0C-CD-01-00-{n:02X}" for n in range(256))
        doc = self.doc(station(gse_macs=macs), "mac_carry.scd")
        self.assertEqual("01-0C-CD-01-01-00", next_mac_address(doc, "GSE"))

    def test_smv_reads_its_own_prefix_and_ignores_gse(self):
        doc = self.doc(station(gse_macs=("01-0C-CD-01-00-00",),
                               smv_macs=("01-0C-CD-04-00-00",)),
                       "mac_smv.scd")
        self.assertEqual("01-0C-CD-04-00-01", next_mac_address(doc, "SMV"))
        self.assertEqual("01-0C-CD-01-00-01", next_mac_address(doc, "GSE"))

    def test_a_gse_address_does_not_block_an_smv_one(self):
        # Different prefixes, so the two pools cannot interfere. Asserted
        # because a scan that read every MAC-Address without minding the
        # prefix would still pass every test above.
        doc = self.doc(station(gse_macs=("01-0C-CD-01-00-00",)), "mac_x.scd")
        self.assertEqual("01-0C-CD-04-00-00", next_mac_address(doc, "SMV"))

    def test_the_result_is_uppercase_and_matches_the_schema_pattern(self):
        doc = self.doc(station(gse_macs=("01-0c-cd-01-00-00",)), "mac_lc.scd")
        got = next_mac_address(doc, "GSE")
        self.assertRegex(got, r"^[0-9A-F]{2}(-[0-9A-F]{2}){5}$")
        # The file wrote it lowercase; it still counts as taken.
        self.assertEqual("01-0C-CD-01-00-01", got)

    def test_a_document_with_no_communication_at_all_allocates_the_first(self):
        doc = self.doc(fx.scl(fx.header()), "mac_empty.scd")
        self.assertEqual("01-0C-CD-01-00-00", next_mac_address(doc, "GSE"))

    def test_an_unknown_service_type_is_refused(self):
        doc = self.doc(name="mac_bad.scd")
        with self.assertRaises(EditRejected) as caught:
            next_mac_address(doc, "GOOSE")
        self.assertIn("GSE", str(caught.exception))

    def test_an_element_where_a_document_belongs_says_so(self):
        doc = self.doc(name="mac_el.scd")
        with self.assertRaises(EditRejected) as caught:
            next_mac_address(doc.root, "GSE")
        self.assertIn("SclDocument", str(caught.exception))


# -- APPIDs -----------------------------------------------------------------

class TestAppId(_Base):
    def test_the_lowest_free_appid_is_returned(self):
        doc = self.doc(station(gse_app_ids=("0000",)), "app_a.scd")
        self.assertEqual("0001", next_app_id(doc, "GSE"))

    def test_smv_starts_at_its_own_band(self):
        doc = self.doc(station(gse_app_ids=("0000",)), "app_smv.scd")
        self.assertEqual("4000", next_app_id(doc, "SMV"))

    def test_a_gse_appid_does_not_block_an_smv_one(self):
        doc = self.doc(station(gse_app_ids=("4000",)), "app_cross.scd")
        # 4000 is outside the GSE band, so it is a file that is already wrong;
        # the SMV allocator still has to see it as taken, because uniqueness
        # is over the values, not over the bands.
        self.assertEqual("4001", next_app_id(doc, "SMV"))
        self.assertEqual("0000", next_app_id(doc, "GSE"))

    def test_type_1a_uses_the_trip_goose_band(self):
        doc = self.doc(name="app_1a.scd")
        self.assertEqual("8000", next_app_id(doc, "GSE", type1a=True))

    def test_there_is_no_type_1a_band_for_sampled_values(self):
        doc = self.doc(name="app_1a_smv.scd")
        with self.assertRaises(EditRejected) as caught:
            next_app_id(doc, "SMV", type1a=True)
        self.assertIn("Type 1A", str(caught.exception))

    def test_the_result_is_four_uppercase_hex_digits(self):
        doc = self.doc(station(gse_app_ids=tuple(
            f"{n:04X}" for n in range(0x2A))), "app_hex.scd")
        got = next_app_id(doc, "GSE")
        self.assertEqual("002A", got)
        self.assertRegex(got, r"^[0-9A-F]{4}$")

    def test_a_lowercase_appid_in_the_file_still_counts_as_taken(self):
        doc = self.doc(station(gse_app_ids=("000a", "0000")), "app_lc.scd")
        self.assertEqual("0001", next_app_id(doc, "GSE"))
        used = {next_app_id(doc, "GSE", taken=[f"{n:04X}" for n in range(1, 10)])}
        self.assertEqual({"000B"}, used)

    def test_an_appid_that_is_not_hex_blocks_nothing(self):
        # `tP_APPID`'s pattern only binds when the file declares `xsi:type`,
        # so this is schema-valid without it -- `address.py` records the same.
        doc = self.doc(station(gse_app_ids=("not hex at all",)), "app_junk.scd")
        self.assertEqual("0000", next_app_id(doc, "GSE"))

    def test_a_full_band_is_refused_rather_than_answered_with_none(self):
        # The reference returns `string | null`; ours raises, because a caller
        # writing None into a required attribute produces a document that
        # fails at the vendor tool.
        doc = self.doc(name="app_full.scd")
        every = [f"{n:04X}" for n in range(0x0000, 0x4000)]
        with self.assertRaises(EditRejected) as caught:
            next_app_id(doc, "GSE", taken=every)
        self.assertIn("in use", str(caught.exception))


# -- the batch record -------------------------------------------------------

class TestBatchRecord(_Base):
    """The divergence from the reference's closure. Inside a compound edit the
    document is out of date -- Q33 §9, Q34 §5 and Q37 §3 -- and `taken` is how
    this package has answered that twice already."""

    def test_two_allocations_in_one_batch_do_not_collide(self):
        doc = self.doc(station(gse_macs=("01-0C-CD-01-00-00",)), "batch_a.scd")
        claimed = set()
        got = []
        for _ in range(3):
            mac = next_mac_address(doc, "GSE", claimed)
            claimed.add(mac)
            got.append(mac)
        self.assertEqual(["01-0C-CD-01-00-01", "01-0C-CD-01-00-02",
                          "01-0C-CD-01-00-03"], got)
        self.assertEqual(3, len(set(got)))

    def test_without_the_record_the_same_value_comes_back_twice(self):
        # The hazard itself, asserted, so the parameter is visibly necessary
        # rather than decorative.
        doc = self.doc(station(gse_macs=("01-0C-CD-01-00-00",)), "batch_b.scd")
        self.assertEqual(next_mac_address(doc, "GSE"),
                         next_mac_address(doc, "GSE"))

    def test_the_record_works_for_every_allocator(self):
        doc = self.doc(name="batch_c.scd")
        self.assertEqual("0001", next_app_id(doc, "GSE", taken=["0000"]))
        parent = self.element('<LDevice inst="LD0"/>')
        self.assertEqual("2", next_ln_inst(parent, "LN", "CSWI", taken=[1]))
        self.assertEqual("newDataSet_1", unique_element_name(
            parent, "DataSet", taken=["newDataSet"]))

    def test_the_record_accepts_values_spelled_loosely(self):
        doc = self.doc(name="batch_d.scd")
        self.assertEqual(
            "01-0C-CD-01-00-01",
            next_mac_address(doc, "GSE", taken=[" 01-0c-cd-01-00-00 "]))

    def test_an_integer_instance_and_a_string_one_mean_the_same(self):
        parent = self.element('<LDevice inst="LD0"/>')
        self.assertEqual(next_ln_inst(parent, "LN", "CSWI", taken=[1, 2]),
                         next_ln_inst(parent, "LN", "CSWI", taken=["1", "2"]))


# -- instance numbers -------------------------------------------------------

class TestLnInst(_Base):
    def ldevice(self, *lns):
        return self.element("<LDevice inst=\"LD0\">" + "".join(lns)
                            + "</LDevice>")

    def test_the_lowest_free_instance_is_returned(self):
        parent = self.ldevice(fx.ln("CSWI", inst="1"))
        self.assertEqual("2", next_ln_inst(parent, "LN", "CSWI"))

    def test_a_hole_is_filled_before_the_end_is_extended(self):
        # 26 of the corpus's 59 (IED, class) blocks have a hole.
        parent = self.ldevice(fx.ln("CSWI", inst="1"), fx.ln("CSWI", inst="3"))
        self.assertEqual("2", next_ln_inst(parent, "LN", "CSWI"))

    def test_another_class_does_not_block_this_one(self):
        parent = self.ldevice(fx.ln("XCBR", inst="1"))
        self.assertEqual("1", next_ln_inst(parent, "LN", "CSWI"))

    def test_the_prefix_is_deliberately_not_minded(self):
        # `tLN`'s identity is prefix + lnClass + inst, so a prefix-aware
        # allocator would find more free numbers -- and SEL writes a prefix
        # naming the SUPERVISED device, a vendor convention this package takes
        # no view on. Scanning by class alone cannot collide.
        parent = self.ldevice(fx.ln("LGOS", inst="1", prefix="Q1_TR1_UPC1"))
        self.assertEqual("2", next_ln_inst(parent, "LN", "LGOS"))

    def test_only_direct_children_are_counted(self):
        # `uniqueLNInLDevice` selects `./scl:LN`, so an LN nested deeper
        # belongs to something else.
        parent = self.ldevice(
            '<Private type="v"><LN lnClass="CSWI" inst="1" lnType="T"/>'
            '</Private>')
        self.assertEqual("1", next_ln_inst(parent, "LN", "CSWI"))

    def test_an_lnode_is_read_through_lninst_instead(self):
        bay = self.element("<Bay name=\"B0\">"
                           + fx.lnode(ln_class="CSWI", ln_inst="1")
                           + "</Bay>")
        self.assertEqual("2", next_ln_inst(bay, "LNode", "CSWI"))
        # ...and reading it as an LN finds nothing, because the tag differs.
        self.assertEqual("1", next_ln_inst(bay, "LN", "CSWI"))

    def test_ln0_is_refused_because_it_has_no_instance_number(self):
        parent = self.ldevice()
        with self.assertRaises(EditRejected) as caught:
            next_ln_inst(parent, "LN0", "LLN0")
        self.assertIn("LN0", str(caught.exception))

    def test_a_non_element_parent_says_so(self):
        with self.assertRaises(EditRejected) as caught:
            next_ln_inst("LDevice", "LN", "CSWI")
        self.assertIn("str", str(caught.exception))

    def test_an_exhausted_range_is_refused(self):
        low, high = LN_INST_RANGE
        parent = self.ldevice(*[fx.ln("CSWI", inst=str(n))
                                for n in range(low, high + 1)])
        with self.assertRaises(EditRejected) as caught:
            next_ln_inst(parent, "LN", "CSWI")
        self.assertIn("in use", str(caught.exception))

    def test_an_instance_above_the_range_neither_helps_nor_blocks(self):
        # `mixed.scd` carries 232 LPDI above 99, up to 167. One of them does
        # not stop 1 being free.
        parent = self.ldevice(fx.ln("LPDI", inst="167"))
        self.assertEqual("1", next_ln_inst(parent, "LN", "LPDI"))


# -- element names ----------------------------------------------------------

class TestUniqueElementName(_Base):
    """**A17b changed the stem and Q39 §0 is why.** A17 shipped
    ``DataSet``/``DataSet_1``, taken from the corpus; the reference documents
    ``new<Tag>_xx`` on the three functions that call its allocator, and A17
    read the allocator's own one-line declaration instead of its callers'."""

    def ln0(self, *children):
        return self.element('<LN0 lnClass="LLN0" lnType="T">'
                            + "".join(children) + "</LN0>")

    def test_the_stem_carries_the_references_new_prefix(self):
        self.assertEqual("newDataSet",
                         unique_element_name(self.ln0(), "DataSet"))
        self.assertEqual("new", ALLOCATED_NAME_PREFIX)

    def test_a_name_equal_to_the_bare_tag_does_not_collide(self):
        # `siemens.scd` really does carry a DataSet named `DataSet`. The
        # prefix means an allocated name cannot land on it.
        parent = self.ln0(fx.dataset("DataSet"))
        self.assertEqual("newDataSet",
                         unique_element_name(parent, "DataSet"))

    def test_the_suffix_starts_at_one(self):
        parent = self.ln0(fx.dataset("newDataSet"))
        self.assertEqual("newDataSet_1",
                         unique_element_name(parent, "DataSet"))

    def test_the_corpus_suffix_sequence_is_reproduced(self):
        # The SUFFIX is the corpus's, even though the prefix is not:
        # siemens.scd writes DataSet, DataSet_1, DataSet_2, DataSet_3.
        parent = self.ln0(fx.dataset("newDataSet"),
                          fx.dataset("newDataSet_1"),
                          fx.dataset("newDataSet_2"))
        self.assertEqual("newDataSet_3",
                         unique_element_name(parent, "DataSet"))

    def test_a_hole_in_the_suffixes_is_filled(self):
        parent = self.ln0(fx.dataset("newDataSet"),
                          fx.dataset("newDataSet_2"))
        self.assertEqual("newDataSet_1",
                         unique_element_name(parent, "DataSet"))

    def test_another_tag_of_the_same_name_does_not_collide(self):
        # `uniqueDataSetInLN0` selects `./scl:DataSet` and
        # `uniqueReportControlInLN0` `./scl:ReportControl` -- separate keys,
        # so the two names may be equal. A16's containers are the other way.
        parent = self.ln0(fx.report_control("newDataSet"))
        self.assertEqual("newDataSet",
                         unique_element_name(parent, "DataSet"))

    def test_it_works_for_every_tag_the_seven_modules_need(self):
        for tag in ("DataSet", "ReportControl", "GSEControl",
                    "SampledValueControl", "LogControl"):
            self.assertEqual(ALLOCATED_NAME_PREFIX + tag,
                             unique_element_name(self.ln0(), tag))

    def test_only_direct_children_are_counted(self):
        parent = self.ln0('<Private type="v">' + fx.dataset("newDataSet")
                          + "</Private>")
        self.assertEqual("newDataSet",
                         unique_element_name(parent, "DataSet"))

    def test_it_cannot_be_exhausted(self):
        parent = self.ln0(fx.dataset("newDataSet"),
                          *[fx.dataset(f"newDataSet_{n}")
                            for n in range(1, 60)])
        self.assertEqual("newDataSet_60",
                         unique_element_name(parent, "DataSet"))

    def test_the_type_id_allocator_keeps_the_suffix_and_not_the_prefix(self):
        # `_fresh_id` starts from the source's own id, which is already the
        # engineer's and already meaningful; prefixing `SEL_LLN0_V01` to
        # `newSEL_LLN0_V01` would lose the only thing making the imported type
        # recognisable. The two allocators share the suffix rule and not the
        # stem rule, deliberately.
        from py61850.scl.data_types import _fresh_id
        self.assertEqual("SEL_LLN0_V01_1",
                         _fresh_id("LNodeType", "SEL_LLN0_V01", set()))

    def test_a_non_element_parent_says_so(self):
        with self.assertRaises(EditRejected) as caught:
            unique_element_name(None, "DataSet")
        self.assertIn("NoneType", str(caught.exception))


# -- what the corpus settles ------------------------------------------------

class TestCorpus(unittest.TestCase):
    """The three exports, and the measurements this module's tables were taken
    from. **Unlike A16 and A16b, this phase has real material** -- 162
    addresses and 13,932 `LN@inst` across three vendors -- so the tables are
    measured rather than inferred, and the day a file breaks one of them this
    fires."""

    FILES = ("sel.scd", "mixed.scd", "siemens.scd")

    def corpus(self, name):
        path = roundtrip.CORPUS / name
        if not path.is_file():
            self.skipTest("the corpus is not in this distribution")
        return SclDocument.parse(path)

    def addresses(self, doc):
        """``[(service, P type, value), ...]`` for every GSE/SMV address."""
        out = []
        for element in doc.root.iter():
            if not isinstance(element.tag, str):
                continue
            if strip_ns(element.tag) not in ("GSE", "SMV"):
                continue
            service = strip_ns(element.tag)
            for address in element:
                if strip_ns(address.tag) != "Address":
                    continue
                for p in address:
                    if strip_ns(p.tag) != "P":
                        continue
                    out.append((service, (p.get("type") or ""),
                                (p.text or "").strip()))
        return out

    def test_every_mac_address_carries_the_prefix_for_its_service(self):
        total = {"GSE": 0, "SMV": 0}
        for name in self.FILES:
            for service, p_type, value in self.addresses(self.corpus(name)):
                if p_type != "MAC-Address":
                    continue
                total[service] += 1
                self.assertTrue(
                    value.upper().startswith(MAC_ADDRESS_PREFIXES[service]),
                    f"{name}: a {service} address is {value}, outside "
                    f"{MAC_ADDRESS_PREFIXES[service]}; the prefix table is "
                    f"measured from these files and now needs re-measuring")
        self.assertEqual({"GSE": 146, "SMV": 16}, total)

    def test_every_appid_falls_in_the_band_for_its_service(self):
        total = {"GSE": 0, "SMV": 0}
        for name in self.FILES:
            for service, p_type, value in self.addresses(self.corpus(name)):
                if p_type != "APPID":
                    continue
                total[service] += 1
                low, high = APP_ID_RANGES[(service, False)]
                self.assertTrue(
                    low <= int(value, 16) <= high,
                    f"{name}: a {service} APPID is {value}, outside "
                    f"{low:#06x}..{high:#06x}")
        self.assertEqual({"GSE": 146, "SMV": 16}, total)

    def test_no_corpus_file_uses_the_type_1a_band(self):
        # The one row of APP_ID_RANGES with no measurement behind it, recorded
        # so that stays visible.
        low, high = APP_ID_RANGES[("GSE", True)]
        for name in self.FILES:
            for _, p_type, value in self.addresses(self.corpus(name)):
                if p_type == "APPID":
                    self.assertFalse(low <= int(value, 16) <= high, name)

    def test_the_corpus_exceeds_the_narrow_mac_block(self):
        # `01-0C-CD-01-00-00`..`01-0C-CD-01-01-FF` is the block as usually
        # published. This is why the ceiling is not taken from it: a file
        # three vendors wrote is already past it.
        highest = max(
            value.upper()
            for name in self.FILES
            for service, p_type, value in self.addresses(self.corpus(name))
            if p_type == "MAC-Address" and service == "GSE")
        self.assertEqual("01-0C-CD-01-09-8E", highest)
        self.assertGreater(int(highest.replace("-", "")[-4:], 16), 0x01FF)

    def test_the_reference_bound_on_ln_inst_is_exceeded_and_kept(self):
        # 232 LN@inst above 99, all LPDI in mixed.scd, up to 167 -- and none
        # of them a supervision node, which is why (1, 99) has never bound an
        # allocation this package made.
        over = []
        supervision_max = 0
        for name in self.FILES:
            doc = self.corpus(name)
            for element in doc.root.iter():
                if not isinstance(element.tag, str) \
                        or strip_ns(element.tag) != "LN":
                    continue
                inst, ln_class = element.get("inst"), element.get("lnClass")
                if not (inst or "").isdigit():
                    continue
                if int(inst) > LN_INST_RANGE[1]:
                    over.append((name, ln_class))
                if ln_class in ("LGOS", "LSVS"):
                    supervision_max = max(supervision_max, int(inst))
        self.assertEqual(232, len(over))
        self.assertEqual({("mixed.scd", "LPDI")}, set(over))
        self.assertEqual(31, supervision_max)
        self.assertLessEqual(supervision_max, LN_INST_RANGE[1])

    def test_the_name_suffix_pattern_is_the_corpus_own(self):
        # 77% of DataSet names, 90% of ReportControl, 91% of GSEControl and
        # 100% of SampledValueControl end in a digit. The stem-then-_n rule is
        # measured, not invented.
        counted = {}
        for tag in ("DataSet", "ReportControl", "GSEControl",
                    "SampledValueControl"):
            total = suffixed = 0
            for name in self.FILES:
                doc = self.corpus(name)
                for element in doc.root.iter():
                    if not isinstance(element.tag, str) \
                            or strip_ns(element.tag) != tag:
                        continue
                    value = element.get("name")
                    if not value:
                        continue
                    total += 1
                    suffixed += bool(re.search(r"\d$", value))
            counted[tag] = (total, suffixed)
        self.assertEqual({"DataSet": (295, 228),
                          "ReportControl": (852, 770),
                          "GSEControl": (146, 134),
                          "SampledValueControl": (16, 16)}, counted)

    def test_allocating_against_a_corpus_file_lands_outside_what_it_uses(self):
        # The end-to-end check: whatever the file holds, what comes back is
        # free in it. Asserted against the document rather than against a
        # fixture, which is the thing A16 and A16b could not do at all.
        doc = self.corpus("mixed.scd")
        used = {value.upper()
                for _, p_type, value in self.addresses(doc)
                if p_type == "MAC-Address"}
        for service in SERVICE_TYPES:
            got = next_mac_address(doc, service)
            self.assertNotIn(got, used)
            self.assertTrue(got.startswith(MAC_ADDRESS_PREFIXES[service]))
        app_ids = {value.upper() for _, p_type, value in self.addresses(doc)
                   if p_type == "APPID"}
        for service in SERVICE_TYPES:
            got = next_app_id(doc, service)
            self.assertNotIn(got, app_ids)
            low, high = APP_ID_RANGES[(service, False)]
            self.assertTrue(low <= int(got, 16) <= high)


# -- nothing here is an edit ------------------------------------------------

class TestNothingIsApplied(_Base):
    """The allocators themselves still build no edit, and the seven modules
    that defer to them are wired as of A17b.

    A17 split the wiring off under `RULES` §5 because three of the seven
    RAISED where they would allocate; this class was the guard that the split
    held, and it now records that it closed."""

    def test_no_allocator_changes_the_document(self):
        doc = self.doc(station(gse_macs=("01-0C-CD-01-00-00",),
                               gse_app_ids=("0000",)), "quiet.scd")
        before = doc.to_bytes()
        next_mac_address(doc, "GSE")
        next_app_id(doc, "GSE")
        next_ln_inst(doc.root, "LN", "CSWI")
        unique_element_name(doc.root, "DataSet")
        self.assertEqual(before, doc.to_bytes())

    def test_the_three_rewired_modules_now_allocate(self):
        # **A17b flipped these**, and the guard moves with them rather than
        # being deleted: what used to assert a refusal naming A17 now asserts
        # that the allocator is reached and that the reference's prefix is
        # what comes out.
        from py61850.scl import (create_data_set, create_report_control,
                                 create_sampled_value_control)
        doc = self.doc(name="now_allocate.scd")
        for function, tag in ((create_data_set, "DataSet"),
                              (create_report_control, "ReportControl"),
                              (create_sampled_value_control,
                               "SampledValueControl")):
            ln0 = ET.fromstring('<LN0 lnClass="LLN0" lnType="T"/>')
            edits = function(doc, ln0)
            created = [e for e in edits
                       if getattr(e, "node", None) is not None
                       and strip_ns(e.node.tag) == tag]
            self.assertEqual(1, len(created), tag)
            self.assertEqual(ALLOCATED_NAME_PREFIX + tag,
                             created[0].node.get("name"))
            # An empty name is still a caller's bug and still raises.
            with self.assertRaises(EditRejected):
                function(doc, ln0, "")


if __name__ == "__main__":
    unittest.main()

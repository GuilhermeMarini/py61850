# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""`py61850.scl.ied` -- renaming an IED, and removing one.

**Four of the six elements a rename must follow are absent from every
reference file.** `LNode`, `ClientLN`, `KDC` and `Association` are zero in all
three exports, so their fixtures are built from the schema in
`scl_fixtures.py` and each says which part of it. Only `ExtRef@iedName` and
`ConnectedAP@iedName` have corpus material -- and they have 4,399 instances of
it, which is what the `TestCorpus` class at the bottom is for.

The built fixtures and the corpus answer different questions and both are
needed: the fixtures pin the behaviour on shapes no vendor happens to write,
and the corpus pins that the behaviour survives 830,000 elements of real
vendor markup.
"""

import tempfile
import unittest
from collections import Counter

from py61850.scl import (
    EditRejected,
    IED_NAME_ELEMENTS,
    ORPHAN_IED_NAME,
    Insert,
    Remove,
    SclDocument,
    SetAttributes,
    control_block_obj_ref,
    insert_ied,
    iter_local,
    lnode_type_conflicts,
    remove_ied,
    strip_ns,
    update_ied,
)
from py61850.scl.controls import CONTROL_BLOCK_TAGS
from py61850.scl.ied import _object_reference, _object_reference_index
from tests.unit import roundtrip
from tests.unit import scl_fixtures as fx

PUB_CB_REF = "PUBLD/LLN0.GC1"
PUB_DS_REF = "PUBLD/LLN0.DS1"


def publisher(name="PUB", ied_names=("SUB",), extra_ln0=""):
    """An IED publishing one GOOSE block over one dataset, with Edition 1
    `IEDName` subscriber records inside the block."""
    body = fx.dataset("DS1", (fx.fcda("LD", "MMXU", "TotW", "MX"),))
    body += fx.gse_control(
        "GC1", dat_set="DS1", app_id="PUB_APP",
        body="".join(fx.ied_name(n, ap_ref="S1") for n in ied_names))
    body += extra_ln0
    return fx.ied(name, fx.access_point(
        "S1", fx.ldevice("LD", fx.ln0(body=body))))


def subscriber(name="SUB", pub="PUB", supervised=True, kdc_for=None,
               association_for=None, client_for=None):
    """An IED subscribing to `pub`, optionally supervising it and optionally
    carrying the three other `iedName` carriers that name it."""
    ext = fx.ext_ref(
        iedName=pub, ldInst="LD", prefix="", lnClass="MMXU", lnInst="1",
        doName="TotW", daName="mag.f", serviceType="GOOSE", srcLDInst="LD",
        srcPrefix="", srcLNClass="LLN0", srcLNInst="", srcCBName="GC1",
        intAddr="IN1")
    ln0_body = fx.inputs(ext)
    if client_for is not None:
        ln0_body += fx.report_control(
            "RC1", dat_set=None,
            body=fx.rpt_enabled_with([fx.client_ln(client_for)]))
    lns = ""
    if supervised:
        lns = fx.supervision(cb_ref=f"{pub}LD/LLN0.GC1",
                             dat_set=f"{pub}LD/LLN0.DS1",
                             go_id="PUB_APP")
    server = fx.ldevice("LD", fx.ln0(body=ln0_body) + lns)
    if association_for is not None:
        server += fx.association(association_for)
    body = fx.access_point("S1", server)
    if kdc_for is not None:
        body += fx.kdc(kdc_for)
    return fx.ied(name, body)


def substation(ied_name="PUB"):
    return ('<Substation name="S"><VoltageLevel name="V"><Bay name="B">'
            + fx.lnode(ied_name) + "</Bay></VoltageLevel></Substation>")


def station(pub="PUB", sub="SUB", **kwargs):
    """The whole document: a publisher, a subscriber that names it five ways,
    a `Communication` section and a `Substation` section with an `LNode`."""
    aps = [fx.connected_ap(pub, "S1"), fx.connected_ap(sub, "S1")]
    return fx.scl(
        fx.header(),
        substation(pub),
        fx.communication(fx.subnetwork("SN", aps)),
        publisher(pub),
        subscriber(sub, pub, kdc_for=pub, association_for=pub,
                   client_for=pub, **kwargs))


class _Base(unittest.TestCase):
    def doc(self, text=None):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        return SclDocument.parse(fx.write(self._dir.name, "a.scd",
                                          text or station()))

    def ied(self, doc, name):
        namespace = doc.root.tag[:doc.root.tag.index("}") + 1] \
            if doc.root.tag.startswith("{") else ""
        for child in doc.root:
            if child.tag == namespace + "IED" and child.get("name") == name:
                return child
        raise AssertionError(f"no IED named {name!r}")

    def rename(self, doc, old, new):
        edits = update_ied(doc, SetAttributes(self.ied(doc, old),
                                              {"name": new}))
        doc.apply_edit(edits)
        return edits

    def names(self, doc, local_name, attribute="iedName"):
        return [el.get(attribute) for el in iter_local(doc.root, local_name)]


# -- what the reference's list of six actually is ---------------------------

class TestTheSixElements(unittest.TestCase):
    """The list is not a convenience, it is the schema's own answer."""

    def test_the_six_are_the_reference_s_six(self):
        self.assertEqual(
            set(IED_NAME_ELEMENTS),
            {"LNode", "ClientLN", "ExtRef", "KDC", "Association",
             "ConnectedAP"})

    def test_the_orphan_value_is_the_four_character_string(self):
        """`tLNode@iedName` defaults to it, `tIEDNameIsNone` is a pattern of
        exactly it, and `tIEDName` is written to exclude it. It is neither
        Python's None nor the absence of the attribute."""
        self.assertIsInstance(ORPHAN_IED_NAME, str)
        self.assertEqual(ORPHAN_IED_NAME, "None")


# -- refusals ---------------------------------------------------------------

class TestUpdateRefusals(_Base):
    def test_a_remove_is_not_a_set_attributes(self):
        doc = self.doc()
        with self.assertRaises(EditRejected) as cm:
            update_ied(doc, Remove(self.ied(doc, "PUB")))
        self.assertIn("SetAttributes", str(cm.exception))

    def test_an_element_that_is_not_an_ied(self):
        doc = self.doc()
        ln0 = next(iter_local(doc.root, "LN0"))
        with self.assertRaises(EditRejected) as cm:
            update_ied(doc, SetAttributes(ln0, {"name": "X"}))
        self.assertIn("LN0", str(cm.exception))

    def test_an_ied_that_is_not_a_child_of_the_root(self):
        """A vendor is free to nest an element called `IED` inside a
        `Private`, and DIGSI does exactly that -- one per real device."""
        text = fx.scl(fx.header(),
                      fx.ied("PUB", '<Private type="v"><IED name="PUB"/>'
                                    "</Private>"))
        doc = self.doc(text)
        nested = [el for el in iter_local(doc.root, "IED")
                  if doc.parent_of(el) is not doc.root]
        self.assertEqual(len(nested), 1)
        with self.assertRaises(EditRejected):
            update_ied(doc, SetAttributes(nested[0], {"name": "X"}))

    def test_an_empty_name_is_refused(self):
        doc = self.doc()
        with self.assertRaises(EditRejected) as cm:
            update_ied(doc, SetAttributes(self.ied(doc, "PUB"), {"name": ""}))
        self.assertIn("required", str(cm.exception))

    def test_the_orphan_sentinel_is_refused_as_a_name(self):
        doc = self.doc()
        with self.assertRaises(EditRejected) as cm:
            update_ied(doc, SetAttributes(self.ied(doc, "PUB"),
                                          {"name": ORPHAN_IED_NAME}))
        self.assertIn("reserved", str(cm.exception))

    def test_a_collision_is_refused_and_names_both(self):
        doc = self.doc()
        with self.assertRaises(EditRejected) as cm:
            update_ied(doc, SetAttributes(self.ied(doc, "PUB"),
                                          {"name": "SUB"}))
        self.assertIn("PUB", str(cm.exception))
        self.assertIn("SUB", str(cm.exception))

    def test_a_refused_rename_changes_nothing(self):
        doc = self.doc()
        before = doc.to_bytes()
        with self.assertRaises(EditRejected):
            update_ied(doc, SetAttributes(self.ied(doc, "PUB"),
                                          {"name": "SUB"}))
        self.assertEqual(doc.to_bytes(), before)


class TestRemoveRefusals(_Base):
    def test_a_set_attributes_is_not_a_remove(self):
        doc = self.doc()
        with self.assertRaises(EditRejected) as cm:
            remove_ied(doc, SetAttributes(self.ied(doc, "PUB"), {"desc": "x"}))
        self.assertIn("Remove", str(cm.exception))

    def test_an_element_that_is_not_an_ied(self):
        doc = self.doc()
        ap = next(iter_local(doc.root, "ConnectedAP"))
        with self.assertRaises(EditRejected) as cm:
            remove_ied(doc, Remove(ap))
        self.assertIn("ConnectedAP", str(cm.exception))


# -- the rename that does nothing -------------------------------------------

class TestRenameThatIsNotOne(_Base):
    def test_an_edit_that_does_not_touch_name_is_returned_alone(self):
        doc = self.doc()
        edit = SetAttributes(self.ied(doc, "PUB"), {"desc": "feeder 1"})
        edits = update_ied(doc, edit)
        self.assertEqual(len(edits), 1)
        self.assertIs(edits[0], edit)

    def test_setting_the_name_it_already_has_is_returned_alone(self):
        doc = self.doc()
        edit = SetAttributes(self.ied(doc, "PUB"), {"name": "PUB"})
        self.assertEqual(update_ied(doc, edit), [edit])

    def test_the_input_edit_comes_back_first(self):
        """Q23's rule: everything the caller should apply, input edit first,
        so applying it is one call and undoing it is one entry."""
        doc = self.doc()
        edit = SetAttributes(self.ied(doc, "PUB"), {"name": "PUB_2"})
        edits = update_ied(doc, edit)
        self.assertIs(edits[0], edit)
        self.assertGreater(len(edits), 1)


# -- the six, one at a time -------------------------------------------------

class TestRenameFollowsTheSix(_Base):
    def test_ext_ref(self):
        doc = self.doc()
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(self.names(doc, "ExtRef"), ["PUB_2"])

    def test_connected_ap(self):
        doc = self.doc()
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(sorted(self.names(doc, "ConnectedAP")),
                         ["PUB_2", "SUB"])

    def test_lnode(self):
        doc = self.doc()
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(self.names(doc, "LNode"), ["PUB_2"])

    def test_client_ln(self):
        doc = self.doc()
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(self.names(doc, "ClientLN"), ["PUB_2"])

    def test_kdc(self):
        doc = self.doc()
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(self.names(doc, "KDC"), ["PUB_2"])

    def test_association(self):
        doc = self.doc()
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(self.names(doc, "Association"), ["PUB_2"])

    def test_all_six_in_one_document_and_nothing_left_behind(self):
        doc = self.doc()
        self.rename(doc, "PUB", "PUB_2")
        left = [strip_ns(el.tag) for el in doc.root.iter()
                if isinstance(el.tag, str) and el.get("iedName") == "PUB"]
        self.assertEqual(left, [])

    def test_the_ied_element_itself_is_the_caller_s_own_edit(self):
        doc = self.doc()
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(self.ied(doc, "PUB_2").get("name"), "PUB_2")

    def test_another_ied_s_references_are_untouched(self):
        doc = self.doc()
        self.rename(doc, "PUB", "PUB_2")
        self.assertIn("SUB", self.names(doc, "ConnectedAP"))


class TestRelativeExtRef(_Base):
    def test_an_at_sign_is_not_a_name_and_is_left_alone(self):
        """`tExtRef@iedName` is `tIEDNameOrRelative`, and `@` means "the IED
        this element is in" -- which no rename changes. No corpus file writes
        one, so this is the schema's word rather than a measurement."""
        text = fx.scl(fx.header(), publisher("PUB"),
                      fx.ied("SUB", fx.access_point("S1", fx.ldevice(
                          "LD", fx.ln0(body=fx.inputs(
                              fx.ext_ref(iedName="@", ldInst="LD",
                                         lnClass="MMXU")))))))
        doc = self.doc(text)
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(self.names(doc, "ExtRef"), ["@"])


# -- the IEDName element ----------------------------------------------------

class TestRenameFollowsIedNameElements(_Base):
    def text_of(self, doc):
        return [(el.text or "") for el in iter_local(doc.root, "IEDName")]

    def test_the_text_is_rewritten(self):
        doc = self.doc(station(pub="PUB", sub="SUB"))
        self.rename(doc, "SUB", "SUB_2")
        self.assertEqual(self.text_of(doc), ["SUB_2"])

    def test_an_ied_name_naming_a_different_ied_is_untouched(self):
        doc = self.doc(fx.scl(fx.header(),
                              publisher("PUB", ied_names=("A", "B")),
                              fx.ied("A"), fx.ied("B")))
        self.rename(doc, "A", "A_2")
        self.assertEqual(self.text_of(doc), ["A_2", "B"])

    def test_surrounding_whitespace_survives(self):
        """No corpus file indents an `IEDName`, but one that did would
        otherwise have its indentation eaten by a rename, and the round trip
        is what this project measures itself on."""
        text = fx.scl(fx.header(),
                      fx.ied("PUB", fx.access_point("S1", fx.ldevice(
                          "LD", fx.ln0(body=fx.gse_control(
                              "GC1", body="<IEDName>\n      SUB\n    "
                                          "</IEDName>"))))),
                      fx.ied("SUB"))
        doc = self.doc(text)
        self.rename(doc, "SUB", "SUB_2")
        self.assertEqual(self.text_of(doc), ["\n      SUB_2\n    "])


# -- supervision ------------------------------------------------------------

def supervision_values(doc, do_name):
    out = []
    for doi in iter_local(doc.root, "DOI"):
        if doi.get("name") != do_name:
            continue
        for dai in doi.iter():
            if strip_ns(dai.tag) != "DAI":
                continue
            for val in dai:
                if strip_ns(val.tag) == "Val":
                    out.append(val.text)
    return out


class TestRenameFollowsSupervision(_Base):
    def test_the_control_block_reference_is_rebuilt(self):
        doc = self.doc()
        self.assertEqual(supervision_values(doc, "GoCBRef"), [PUB_CB_REF])
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(supervision_values(doc, "GoCBRef"),
                         ["PUB_2LD/LLN0.GC1"])

    def test_the_dataset_reference_is_rebuilt(self):
        doc = self.doc()
        self.assertEqual(supervision_values(doc, "DatSet"), [PUB_DS_REF])
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(supervision_values(doc, "DatSet"),
                         ["PUB_2LD/LLN0.DS1"])

    def test_the_sampled_value_reference_is_rebuilt(self):
        text = fx.scl(fx.header(), publisher("PUB"),
                      fx.ied("SUB", fx.access_point("S1", fx.ldevice(
                          "LD", fx.ln0() + fx.supervision(
                              ln_class="LSVS", cb_ref=PUB_CB_REF)))))
        doc = self.doc(text)
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(supervision_values(doc, "SvCBRef"),
                         ["PUB_2LD/LLN0.GC1"])

    def test_the_go_id_is_not_a_reference_and_is_left_alone(self):
        """All 191 `GoID` values in the corpus equal a real
        `GSEControl@appID`. SEL builds its application ids out of the IED
        name; renaming the device does not rename the appID, so a prefix
        sweep would corrupt 191 values that are correct."""
        doc = self.doc()
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(supervision_values(doc, "GoID"), ["PUB_APP"])

    def test_the_app_id_on_the_block_itself_is_left_alone(self):
        doc = self.doc()
        self.rename(doc, "PUB", "PUB_2")
        block = next(iter_local(doc.root, "GSEControl"))
        self.assertEqual(block.get("appID"), "PUB_APP")

    def test_a_value_naming_nothing_in_the_document_is_left_alone(self):
        """A reference this document cannot account for is not this
        function's to rewrite -- it may name a block in another file."""
        text = fx.scl(fx.header(), publisher("PUB"),
                      fx.ied("SUB", fx.access_point("S1", fx.ldevice(
                          "LD", fx.ln0() + fx.supervision(
                              cb_ref="PUBOTHER/LLN0.NOPE")))))
        doc = self.doc(text)
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(supervision_values(doc, "GoCBRef"),
                         ["PUBOTHER/LLN0.NOPE"])

    def test_an_empty_value_is_left_alone(self):
        """The idle shape `sel.scd` writes 24 times."""
        text = fx.scl(fx.header(), publisher("PUB"),
                      fx.ied("SUB", fx.access_point("S1", fx.ldevice(
                          "LD", fx.ln0() + fx.supervision()))))
        doc = self.doc(text)
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(supervision_values(doc, "GoCBRef"), [None])

    def test_supervision_of_a_different_ied_is_left_alone(self):
        text = fx.scl(fx.header(), publisher("PUB"), publisher("OTHER"),
                      fx.ied("SUB", fx.access_point("S1", fx.ldevice(
                          "LD", fx.ln0() + fx.supervision(
                              cb_ref="OTHERLD/LLN0.GC1")))))
        doc = self.doc(text)
        self.rename(doc, "PUB", "PUB_2")
        self.assertEqual(supervision_values(doc, "GoCBRef"),
                         ["OTHERLD/LLN0.GC1"])


class TestAmbiguousDecomposition(_Base):
    """The case the concatenation makes possible, and no corpus file has."""

    def document(self):
        # `R1` + LDevice `XLD` and `R1X` + LDevice `LD` build the SAME LDName,
        # so `R1XLD/LLN0.GC1` does not say which device it means.
        return fx.scl(
            fx.header(),
            fx.ied("R1", fx.access_point("S1", fx.ldevice(
                "XLD", fx.ln0(body=fx.gse_control("GC1"))))),
            fx.ied("R1X", fx.access_point("S1", fx.ldevice(
                "LD", fx.ln0(body=fx.gse_control("GC1"))))),
            fx.ied("SUB", fx.access_point("S1", fx.ldevice(
                "LD", fx.ln0() + fx.supervision(cb_ref="R1XLD/LLN0.GC1")))))

    def test_the_two_really_do_collide(self):
        doc = self.doc(self.document())
        refs = [control_block_obj_ref(doc, el)
                for el in iter_local(doc.root, "GSEControl")]
        self.assertEqual(refs, ["R1XLD/LLN0.GC1", "R1XLD/LLN0.GC1"])

    def test_the_index_records_the_collision_rather_than_picking_one(self):
        doc = self.doc(self.document())
        self.assertIsNone(_object_reference_index(doc)["R1XLD/LLN0.GC1"])

    def test_neither_rename_touches_the_ambiguous_value(self):
        """Declining to guess is the only answer that cannot make an already
        invalid document worse."""
        for old, new in (("R1", "R1_2"), ("R1X", "R1X_2")):
            doc = self.doc(self.document())
            self.rename(doc, old, new)
            self.assertEqual(supervision_values(doc, "GoCBRef"),
                             ["R1XLD/LLN0.GC1"])

    def test_two_blocks_in_ONE_ied_still_resolve(self):
        """The owner is not in doubt, and the owner is all this module
        reads."""
        text = fx.scl(
            fx.header(),
            fx.ied("R1", fx.access_point("S1", fx.ldevice(
                "LD", fx.ln0(body=fx.gse_control("GC1"))))
                + fx.access_point("S2", fx.ldevice(
                    "LD", fx.ln0(body=fx.gse_control("GC1"))))),
            fx.ied("SUB", fx.access_point("S1", fx.ldevice(
                "LD", fx.ln0() + fx.supervision(cb_ref="R1LD/LLN0.GC1")))))
        doc = self.doc(text)
        self.assertEqual(_object_reference_index(doc)["R1LD/LLN0.GC1"], "R1")
        self.rename(doc, "R1", "R1_2")
        self.assertEqual(supervision_values(doc, "GoCBRef"),
                         ["R1_2LD/LLN0.GC1"])


class TestObjectReferenceConstruction(_Base):
    def test_it_agrees_with_a9_on_a_control_block(self):
        doc = self.doc()
        block = next(iter_local(doc.root, "GSEControl"))
        self.assertEqual(_object_reference(doc, block)[0],
                         control_block_obj_ref(doc, block))

    def test_a_dataset_is_built_the_same_way(self):
        doc = self.doc()
        data_set = next(iter_local(doc.root, "DataSet"))
        self.assertEqual(_object_reference(doc, data_set), (PUB_DS_REF, "PUB"))


# -- removal ----------------------------------------------------------------

class TestRemoveIed(_Base):
    def removed(self, doc=None, name="PUB"):
        doc = doc or self.doc()
        edits = remove_ied(doc, Remove(self.ied(doc, name)))
        doc.apply_edit(edits)
        return doc, edits

    def test_the_ied_goes(self):
        doc, _ = self.removed()
        self.assertEqual([el.get("name")
                          for el in iter_local(doc.root, "IED")], ["SUB"])

    def test_the_input_edit_comes_back_first(self):
        doc = self.doc()
        edit = Remove(self.ied(doc, "PUB"))
        edits = remove_ied(doc, edit)
        self.assertIs(edits[0], edit)

    def test_the_connected_ap_goes(self):
        doc, _ = self.removed()
        self.assertEqual(self.names(doc, "ConnectedAP"), ["SUB"])

    def test_the_subnetwork_is_left_standing(self):
        """Removing the last `ConnectedAP` from one is valid SCL, and a
        subnetwork describes the network rather than the devices on it."""
        doc, _ = self.removed()
        self.assertEqual(len(list(iter_local(doc.root, "SubNetwork"))), 1)

    def test_the_client_ln_goes(self):
        doc, _ = self.removed()
        self.assertEqual(self.names(doc, "ClientLN"), [])

    def test_the_kdc_goes(self):
        doc, _ = self.removed()
        self.assertEqual(self.names(doc, "KDC"), [])

    def test_the_association_goes(self):
        doc, _ = self.removed()
        self.assertEqual(self.names(doc, "Association"), [])

    def test_the_ied_name_record_goes(self):
        doc = self.doc()
        self.assertEqual(len(list(iter_local(doc.root, "IEDName"))), 1)
        doc, _ = self.removed(doc=self.doc(), name="SUB")
        self.assertEqual(len(list(iter_local(doc.root, "IEDName"))), 0)

    def test_the_lnode_is_orphaned_and_not_removed(self):
        """A bay's logical node outlives the device allocated to it."""
        doc, _ = self.removed()
        self.assertEqual(self.names(doc, "LNode"), [ORPHAN_IED_NAME])

    def test_the_subscriber_is_unsubscribed_and_kept(self):
        """The fixture's ExtRef carries `intAddr`, so A8 blanks it rather than
        removing it -- the internal address outlives the connection."""
        doc, _ = self.removed()
        ext = next(iter_local(doc.root, "ExtRef"))
        self.assertIsNone(ext.get("iedName"))
        self.assertIsNone(ext.get("srcCBName"))
        self.assertEqual(ext.get("intAddr"), "IN1")

    def test_an_ext_ref_without_int_addr_is_removed(self):
        text = fx.scl(fx.header(), publisher("PUB"),
                      fx.ied("SUB", fx.access_point("S1", fx.ldevice(
                          "LD", fx.ln0(body=fx.inputs(fx.ext_ref(
                              iedName="PUB", ldInst="LD",
                              lnClass="MMXU")))))))
        doc, _ = self.removed(doc=self.doc(text))
        self.assertEqual(len(list(iter_local(doc.root, "ExtRef"))), 0)
        self.assertEqual(len(list(iter_local(doc.root, "Inputs"))), 0)

    def test_supervision_is_blanked_to_the_shape_the_corpus_writes(self):
        doc, _ = self.removed()
        self.assertEqual(supervision_values(doc, "GoCBRef"), [None])
        self.assertEqual(supervision_values(doc, "DatSet"), [None])

    def test_the_supervision_logical_node_is_kept(self):
        """`sel.scd` carries 24 `LGOS` already written this way. Removing the
        node instead would renumber the `inst` of the ones after it."""
        doc, _ = self.removed()
        self.assertEqual(
            [el.get("lnClass") for el in iter_local(doc.root, "LN")], ["LGOS"])

    def test_the_go_id_is_left_alone_on_a_removal_too(self):
        doc, _ = self.removed()
        self.assertEqual(supervision_values(doc, "GoID"), ["PUB_APP"])

    def test_nothing_inside_the_removed_ied_is_edited_separately(self):
        """Its own ExtRefs, datasets and blocks go with the subtree, so the
        expansion must not also name them."""
        doc = self.doc(station(pub="PUB", sub="SUB"))
        pub = self.ied(doc, "PUB")
        inside = set(pub.iter())
        for edit in remove_ied(doc, Remove(pub))[1:]:
            # `getattr(edit, "element", None) or edit.node` would be wrong:
            # an `Element` with no children is FALSY, so a childless one
            # would fall through to the branch that does not exist.
            element = edit.node if isinstance(edit, Remove) else edit.element
            self.assertNotIn(element, inside)

    def test_an_unnamed_ied_is_removed_alone(self):
        """Nothing can refer to it: `iedName` is required wherever it is not
        defaulted, and where it is defaulted it defaults to the sentinel."""
        doc = self.doc(fx.scl(fx.header(), "<IED/>"))
        ied = next(iter_local(doc.root, "IED"))
        self.assertEqual(len(remove_ied(doc, Remove(ied))), 1)

    def test_no_dangling_reference_is_left(self):
        doc, _ = self.removed()
        left = [strip_ns(el.tag) for el in doc.root.iter()
                if isinstance(el.tag, str)
                and el.get("iedName") not in (None, "SUB", ORPHAN_IED_NAME)]
        self.assertEqual(left, [])


# -- invertibility ----------------------------------------------------------

class TestInvertible(_Base):
    def test_a_rename_inverts_to_the_byte(self):
        doc = self.doc()
        before = doc.to_bytes()
        undo = doc.apply_edit(update_ied(
            doc, SetAttributes(self.ied(doc, "PUB"), {"name": "PUB_2"})))
        self.assertNotEqual(doc.to_bytes(), before)
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)

    def test_a_removal_inverts_to_the_byte(self):
        doc = self.doc()
        before = doc.to_bytes()
        undo = doc.apply_edit(remove_ied(doc, Remove(self.ied(doc, "PUB"))))
        self.assertNotEqual(doc.to_bytes(), before)
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)

    def test_a_rename_and_a_rename_back_is_the_file_it_was(self):
        doc = self.doc()
        before = doc.to_bytes()
        self.rename(doc, "PUB", "PUB_2")
        self.rename(doc, "PUB_2", "PUB")
        self.assertEqual(doc.to_bytes(), before)


# -- namespace exactness ----------------------------------------------------

SEL = "http://www.selinc.com/2006/61850"


class TestNamespaceExactness(_Base):
    """A vendor's element of the same local name is not the element."""

    def document(self):
        private = (f'<Private type="SEL"><sel:GooseSubscription '
                   f'xmlns:sel="{SEL}" iedName="PUB" '
                   f'goCbRef="PUBLD/LLN0$GO$GC1"/></Private>')
        vendor_ext = (f'<sel:ExtRef xmlns:sel="{SEL}" iedName="PUB" '
                      f'srcCBName="GC1"/>')
        return fx.scl(
            fx.header(), publisher("PUB"),
            fx.ied("SUB", private + fx.access_point("S1", fx.ldevice(
                "LD", fx.ln0(body=fx.inputs(
                    fx.ext_ref(iedName="PUB", ldInst="LD", lnClass="MMXU",
                               intAddr="IN1") + vendor_ext))))))

    def test_the_standard_ext_ref_is_followed(self):
        doc = self.doc(self.document())
        self.rename(doc, "PUB", "PUB_2")
        standard = [el for el in iter_local(doc.root, "ExtRef")
                    if not el.tag.startswith("{" + SEL)]
        self.assertEqual([el.get("iedName") for el in standard], ["PUB_2"])

    def test_the_vendor_ext_ref_of_the_same_local_name_is_not(self):
        """29 of `sel.scd`'s 969 elements called `ExtRef` are this."""
        doc = self.doc(self.document())
        self.rename(doc, "PUB", "PUB_2")
        vendor = [el for el in iter_local(doc.root, "ExtRef")
                  if el.tag.startswith("{" + SEL)]
        self.assertEqual([el.get("iedName") for el in vendor], ["PUB"])

    def test_a_vendor_subscription_is_left_whole_rather_than_half_rewritten(self):
        """Following `iedName` here and not `goCbRef` beside it would leave an
        element internally inconsistent in a way the untouched one is not."""
        doc = self.doc(self.document())
        self.rename(doc, "PUB", "PUB_2")
        sub = next(iter_local(doc.root, "GooseSubscription"))
        self.assertEqual(sub.get("iedName"), "PUB")
        self.assertEqual(sub.get("goCbRef"), "PUBLD/LLN0$GO$GC1")

    def test_a_removal_leaves_the_vendor_half_to_the_vendor_library_too(self):
        doc = self.doc(self.document())
        doc.apply_edit(remove_ied(doc, Remove(self.ied(doc, "PUB"))))
        kept = {("vendor" if el.tag.startswith("{" + SEL) else "standard"):
                el.get("iedName") for el in iter_local(doc.root, "ExtRef")}
        # The standard one is unsubscribed -- blanked rather than removed,
        # because it carries `intAddr`. The vendor one is not this library's.
        self.assertEqual(kept, {"standard": None, "vendor": "PUB"})


# -- Q14: the lazy caches ---------------------------------------------------

class TestCacheInvalidation(_Base):
    """A5 created the hazard, A9, A11 and A12 each met it again, and A13 is
    where it stops being tolerable: `remove_ied` removes the very element
    `doc.ied(name)` wraps."""

    def test_removing_an_ied_stops_it_being_served_from_cache(self):
        doc = self.doc()
        self.assertIsNotNone(doc.ied("PUB"))
        doc.apply_edit(remove_ied(doc, Remove(self.ied(doc, "PUB"))))
        self.assertIsNone(doc.ied("PUB"))
        self.assertNotIn("PUB", doc.ied_names)

    def test_renaming_an_ied_moves_the_cache_key(self):
        doc = self.doc()
        self.assertEqual(doc.ied("PUB").name, "PUB")
        self.rename(doc, "PUB", "PUB_2")
        self.assertIsNone(doc.ied("PUB"))
        self.assertEqual(doc.ied("PUB_2").name, "PUB_2")

    def test_an_attribute_edit_invalidates_too_because_models_snapshot(self):
        """The narrower rule -- only Insert and Remove invalidate -- is wrong
        here, and the models say so themselves: `IedHeader` copies `desc` in
        its constructor."""
        doc = self.doc()
        self.assertIsNone(doc.ied_headers["PUB"].desc)
        doc.apply_edit(SetAttributes(self.ied(doc, "PUB"), {"desc": "feeder"}))
        self.assertEqual(doc.ied_headers["PUB"].desc, "feeder")

    def test_an_edit_in_the_communication_section_invalidates_it(self):
        doc = self.doc()
        ap = next(iter_local(doc.root, "ConnectedAP"))
        self.assertEqual(doc.communication.subnetworks[0].connected_aps[0].ap_name, "S1")
        doc.apply_edit(SetAttributes(ap, {"apName": "S2"}))
        self.assertEqual(doc.communication.subnetworks[0].connected_aps[0].ap_name, "S2")

    def test_editing_one_ied_leaves_another_warm(self):
        """What ancestry buys over clearing everything: the other 57 IEDs of a
        station are not re-warmed to edit one."""
        doc = self.doc()
        warm = doc.ied("SUB")
        doc.apply_edit(SetAttributes(self.ied(doc, "PUB"), {"desc": "x"}))
        self.assertIs(doc.ied("SUB"), warm)

    def test_an_edit_outside_every_cached_section_invalidates_nothing(self):
        doc = self.doc()
        warm = doc.ied("PUB")
        lnode = next(iter_local(doc.root, "LNode"))
        doc.apply_edit(SetAttributes(lnode, {"lnInst": "2"}))
        self.assertIs(doc.ied("PUB"), warm)

    def test_the_parent_map_is_not_dropped_with_the_rest(self):
        """It is maintained by the applier, not rebuilt by it: dropping it
        would put a 98 ms walk behind every root-level edit."""
        doc = self.doc()
        ext = next(iter_local(doc.root, "ExtRef"))
        doc.apply_edit(remove_ied(doc, Remove(self.ied(doc, "PUB"))))
        self.assertIn("parents", doc._cache)
        self.assertIsNotNone(doc.parent_of(ext))

    def test_a_rejected_edit_leaves_the_cache_alone(self):
        doc = self.doc()
        warm = doc.ied("PUB")
        with self.assertRaises(EditRejected):
            doc.apply_edit(SetAttributes(doc.root, {"": "x"}))
        self.assertIs(doc.ied("PUB"), warm)


# -- the reference corpus ---------------------------------------------------

class TestCorpus(unittest.TestCase):
    """830,000 elements of real vendor markup, and the measurements the
    module's decisions were taken on."""

    FILES = ("sel.scd", "mixed.scd", "siemens.scd")

    def corpus(self, name):
        path = roundtrip.CORPUS / name
        if not path.is_file():
            self.skipTest("the corpus is not in this distribution")
        return SclDocument.parse(path)

    def namespace(self, doc):
        tag = doc.root.tag
        return tag[:tag.index("}") + 1] if tag.startswith("{") else ""

    def ieds(self, doc):
        tag = self.namespace(doc) + "IED"
        return [child for child in doc.root if child.tag == tag]

    def test_the_corpus_holds_fifty_eight_real_ieds(self):
        self.assertEqual(sum(len(self.ieds(self.corpus(n)))
                             for n in self.FILES), 58)

    def test_local_name_finds_fourteen_that_are_not_ieds(self):
        """Q29's measurement, from A13's side: `siemens.scd` holds 14 elements
        called `IED` in the Siedig namespace, one per real device. Selecting
        by local name picks a vendor's bookkeeping half the time."""
        doc = self.corpus("siemens.scd")
        by_local = list(iter_local(doc.root, "IED"))
        self.assertEqual(len(by_local), 28)
        self.assertEqual(len(self.ieds(doc)), 14)

    def test_every_supervision_reference_resolves_to_exactly_one_owner(self):
        """**758 of 758, and 0 dangling.** This is what makes resolution
        possible where decomposition is not: 549 control-block references and
        209 dataset references, every one naming an element that is really in
        the file."""
        total = 0
        for name in self.FILES:
            doc = self.corpus(name)
            index = _object_reference_index(doc)
            from py61850.scl.ied import _supervision_values
            for _, text in _supervision_values(doc):
                if not text:
                    continue
                total += 1
                self.assertIn(text, index, f"{name}: {text}")
                self.assertIsNotNone(index[text], f"{name}: {text} ambiguous")
        self.assertEqual(total, 758)

    def test_no_corpus_ied_name_is_a_prefix_of_another(self):
        """Which is why the ambiguous case is pinned on a built fixture: it is
        not merely rare here, it is absent."""
        for name in self.FILES:
            names = [el.get("name") for el in self.ieds(self.corpus(name))]
            pairs = [(a, b) for a in names for b in names
                     if a != b and b.startswith(a)]
            self.assertEqual(pairs, [], name)

    def test_the_object_reference_agrees_with_a9_on_every_block(self):
        checked = 0
        for name in self.FILES:
            doc = self.corpus(name)
            namespace = self.namespace(doc)
            for local_name in CONTROL_BLOCK_TAGS:
                for block in doc.root.iter(namespace + local_name):
                    expected = control_block_obj_ref(doc, block)
                    if expected is None:
                        continue
                    self.assertEqual(_object_reference(doc, block)[0], expected)
                    checked += 1
        self.assertEqual(checked, 1014)

    def test_a_rename_leaves_no_standard_namespace_reference_behind(self):
        """The 4,399 `ExtRef`/`ConnectedAP` references are the exercised half
        of the six; this asserts the sweep over all of them at once."""
        for name in self.FILES:
            doc = self.corpus(name)
            namespace = self.namespace(doc)
            target = max(self.ieds(doc), key=lambda el: sum(
                1 for e in doc.root.iter(namespace + "ExtRef")
                if e.get("iedName") == el.get("name")))
            old = target.get("name")
            doc.apply_edit(update_ied(doc, SetAttributes(
                target, {"name": "RENAMED_BY_A13"})))
            left = [el.tag for el in doc.root.iter()
                    if isinstance(el.tag, str)
                    and el.tag.startswith(namespace)
                    and el.get("iedName") == old]
            self.assertEqual(left, [], f"{name}: {old}")

    def test_a_rename_round_trips_to_the_byte(self):
        for name in self.FILES:
            doc = self.corpus(name)
            before = doc.to_bytes()
            target = self.ieds(doc)[0]
            undo = doc.apply_edit(update_ied(doc, SetAttributes(
                target, {"name": "RENAMED_BY_A13"})))
            self.assertNotEqual(doc.to_bytes(), before, name)
            doc.apply_edit(undo)
            self.assertEqual(doc.to_bytes(), before, name)

    def test_a_removal_leaves_no_standard_namespace_reference_behind(self):
        for name in self.FILES:
            doc = self.corpus(name)
            namespace = self.namespace(doc)
            target = max(self.ieds(doc), key=lambda el: sum(
                1 for e in doc.root.iter(namespace + "ExtRef")
                if e.get("iedName") == el.get("name")))
            old = target.get("name")
            doc.apply_edit(remove_ied(doc, Remove(target)))
            left = [el.tag for el in doc.root.iter()
                    if isinstance(el.tag, str)
                    and el.tag.startswith(namespace)
                    and el.get("iedName") == old]
            self.assertEqual(left, [], f"{name}: {old}")

    def test_a_removal_round_trips_to_the_byte(self):
        for name in self.FILES:
            doc = self.corpus(name)
            before = doc.to_bytes()
            undo = doc.apply_edit(remove_ied(doc, Remove(self.ieds(doc)[0])))
            self.assertNotEqual(doc.to_bytes(), before, name)
            doc.apply_edit(undo)
            self.assertEqual(doc.to_bytes(), before, name)

    def test_removing_every_ied_in_turn_leaves_the_file_it_was(self):
        """58 removals, each undone, each byte-checked. The expansion is the
        largest single compound edit in this library -- one IED in
        `mixed.scd` accounts for 408 `ExtRef` subscribers on its own."""
        for name in self.FILES:
            doc = self.corpus(name)
            before = doc.to_bytes()
            for index in range(len(self.ieds(doc))):
                target = self.ieds(doc)[index]
                undo = doc.apply_edit(remove_ied(doc, Remove(target)))
                doc.apply_edit(undo)
            self.assertEqual(doc.to_bytes(), before, name)

    def test_the_vendor_carriers_the_sweep_declines_to_touch(self):
        """220 `GooseSubscription` and 29 SEL `ExtRef` carry an `iedName` this
        module leaves alone, and the module docstring says why: the same
        elements carry `goCbRef` and `datSetRef` that an `iedName` sweep would
        not follow, leaving them half-rewritten."""
        counted = 0
        for name in self.FILES:
            doc = self.corpus(name)
            namespace = self.namespace(doc)
            counted += sum(
                1 for el in doc.root.iter()
                if isinstance(el.tag, str)
                and not el.tag.startswith(namespace)
                and el.get("iedName") is not None)
        self.assertEqual(counted, 249)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


# -- inserting --------------------------------------------------------------

def importable(name="R1", subnet="SRCNET", subnet_type="8-MMS",
               lnode="T_LLN0", do="SPS_1", aps=("S1",), ext_refs=(),
               ln_types=()):
    """A source document holding one IED, its access points and its types.

    Deliberately small: what the corpus cannot show is a target with no
    `Communication` at all, or a `SubNetwork` that exists and is empty, and
    those are what these fixtures are for. `TestInsertCorpus` does the real
    thing -- 8,967 elements and 319 templates.
    """
    body = fx.ln0(ln_type=lnode, body=fx.inputs(*ext_refs) if ext_refs else "")
    for index, ln_type in enumerate(ln_types, start=1):
        body += fx.ln("MMXU", inst=str(index), ln_type=ln_type)
    ied_body = "".join(
        fx.access_point(ap, fx.ldevice("LD", body) if ap == aps[0] else "")
        for ap in aps)
    types = [fx.lnode_type(lnode, "LLN0", dos=(("Beh", do),)),
             fx.do_type(do, cdc="SPS")]
    for ln_type in ln_types:
        types.append(fx.lnode_type(ln_type, "MMXU", dos=(("Beh", do),)))
    return fx.scl(
        fx.header(),
        fx.communication(fx.subnetwork(
            subnet, tuple(fx.connected_ap(name, ap) for ap in aps),
            type_=subnet_type)),
        fx.ied(name, ied_body),
        fx.templates(*types))


class _Insert(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self._n = 0

    def doc(self, text):
        self._n += 1
        return SclDocument.parse(
            fx.write(self._dir.name, f"d{self._n}.scd", text))

    def source(self, **kwargs):
        return self.doc(importable(**kwargs))

    def target(self, *sections):
        return self.doc(fx.scl(fx.header(), *sections))

    def sections(self, doc):
        return [strip_ns(child.tag) for child in doc.root]

    def subnets(self, doc):
        out = []
        for child in doc.root:
            if strip_ns(child.tag) != "Communication":
                continue
            for subnet in child:
                aps = [ap.get("iedName") for ap in subnet
                       if strip_ns(ap.tag) == "ConnectedAP"]
                out.append((subnet.get("name"), subnet.get("type"), aps))
        return out

    def ieds(self, doc):
        return [child.get("name") for child in doc.root
                if strip_ns(child.tag) == "IED"]

    def pool(self, doc):
        for child in doc.root:
            if strip_ns(child.tag) == "DataTypeTemplates":
                return {(strip_ns(t.tag), t.get("id")) for t in child}
        return set()


class TestInsertRefusals(_Insert):
    def test_a_bare_string_is_refused_rather_than_read_per_character(self):
        """`names` is a sequence, as `import_lnode_types`'s `ids` is, and for
        the same reason: ``"R1"`` would otherwise import ``R`` and ``1``."""
        with self.assertRaises(EditRejected) as cm:
            insert_ied(self.target(), self.source(), "R1")
        self.assertIn("one character at a time", str(cm.exception))

    def test_a_name_the_source_does_not_carry(self):
        with self.assertRaises(EditRejected) as cm:
            insert_ied(self.target(), self.source(), ["NOPE"])
        self.assertIn("NOPE", str(cm.exception))

    def test_a_name_given_twice_in_one_call(self):
        with self.assertRaises(EditRejected) as cm:
            insert_ied(self.target(), self.source(), ["R1", "R1"])
        self.assertIn("twice", str(cm.exception))

    def test_a_name_the_target_already_holds_is_refused_not_renamed(self):
        """The reference checks nothing -- it returns `Insert[]`. Ours refuses,
        because `update_ied` refuses the same collision, because renaming would
        need an allocator that is A17's, and because a device name is the
        engineer's rather than this function's to invent."""
        target = self.target(fx.ied("R1"))
        with self.assertRaises(EditRejected) as cm:
            insert_ied(target, self.source(), ["R1"])
        self.assertIn("already holds an IED named 'R1'", str(cm.exception))
        self.assertIn("update_ied", str(cm.exception))

    def test_a_source_that_is_not_a_document(self):
        """The message is `data_types`' own, so there is one explanation of
        why a source has to be a document and not an element."""
        target = self.target()
        with self.assertRaises(EditRejected) as cm:
            insert_ied(target, target.root, ["R1"])
        self.assertIn("SclDocument", str(cm.exception))

    def test_a_source_in_another_namespace(self):
        source = self.doc(importable().replace(
            "http://www.iec.ch/61850/2003/SCL",
            "http://www.iec.ch/61850/2003/SCL2"))
        with self.assertRaises(EditRejected) as cm:
            insert_ied(self.target(), source, ["R1"])
        self.assertIn("namespace", str(cm.exception))

    def test_an_unknown_policy_is_refused_before_anything_is_read(self):
        with self.assertRaises(EditRejected) as cm:
            insert_ied(self.target(), self.source(), ["R1"],
                       on_conflict="clobber")
        self.assertIn("on_conflict", str(cm.exception))

    def test_a_conflicting_type_refuses_by_default(self):
        """`on_conflict` is passed through unchanged, so the default that Q34
        argued for is the default here without being re-decided."""
        target = self.target(fx.templates(
            fx.lnode_type("T_LLN0", "LLN0", dos=(("Beh", "OTHER"),)),
            fx.do_type("OTHER", cdc="SPS")))
        with self.assertRaises(EditRejected) as cm:
            insert_ied(target, self.source(), ["R1"])
        self.assertIn("T_LLN0", str(cm.exception))

    def test_a_rejected_insert_changes_nothing(self):
        target = self.target(fx.ied("R1"))
        before = target.to_bytes()
        with self.assertRaises(EditRejected):
            insert_ied(target, self.source(), ["R1"])
        self.assertEqual(target.to_bytes(), before)


class TestInsertReturnsOnlyInserts(_Insert):
    def test_every_edit_is_an_insert_as_the_reference_declares(self):
        """`insertIed` is declared to return `Insert[]` -- no `Remove`, no
        `SetAttributes`. Ours matches under the default policy, which is what
        makes the whole import invert to a plain removal."""
        edits = insert_ied(self.target(), self.source(), ["R1"],
                           add_communication=True)
        self.assertTrue(all(isinstance(e, Insert) for e in edits))

    def test_overwrite_is_the_one_policy_that_also_removes(self):
        """The divergence is `on_conflict`'s, not this function's: replacing a
        target type means taking the old one out first."""
        target = self.target(fx.templates(
            fx.lnode_type("T_LLN0", "LLN0", dos=(("Beh", "OTHER"),)),
            fx.do_type("OTHER", cdc="SPS")))
        edits = insert_ied(target, self.source(), ["R1"],
                           on_conflict="overwrite")
        self.assertTrue(any(isinstance(e, Remove) for e in edits))


class TestInsertCopies(_Insert):
    def test_the_ied_arrives_with_its_whole_subtree(self):
        target, source = self.target(), self.source()
        target.apply_edit(insert_ied(target, source, ["R1"]))
        self.assertEqual(self.ieds(target), ["R1"])
        inserted = next(c for c in target.root if strip_ns(c.tag) == "IED")
        self.assertEqual([strip_ns(e.tag) for e in inserted.iter()][:4],
                         ["IED", "AccessPoint", "Server", "LDevice"])

    def test_the_source_document_is_not_touched(self):
        """The reference MOVES its elements, because a browser has
        `importNode` and its source is a throwaway parse of an upload. Ours
        copies -- Q31 §6 -- so the caller may go on using the source."""
        target, source = self.target(), self.source()
        before = source.to_bytes()
        target.apply_edit(insert_ied(target, source, ["R1"],
                                     add_communication=True))
        self.assertEqual(source.to_bytes(), before)

    def test_the_inserted_element_is_not_the_source_element(self):
        target, source = self.target(), self.source()
        original = next(c for c in source.root if strip_ns(c.tag) == "IED")
        target.apply_edit(insert_ied(target, source, ["R1"]))
        inserted = next(c for c in target.root if strip_ns(c.tag) == "IED")
        self.assertIsNot(inserted, original)

    def test_the_types_come_with_it(self):
        target, source = self.target(), self.source()
        self.assertEqual(self.pool(target), set())
        target.apply_edit(insert_ied(target, source, ["R1"]))
        self.assertEqual(self.pool(target),
                         {("LNodeType", "T_LLN0"), ("DOType", "SPS_1")})

    def test_two_ieds_sharing_a_type_insert_it_once(self):
        """The whole reason `names` is a list. As two calls each would plan
        against a target that does not yet hold what the other is inserting
        and each would emit an Insert for the same type."""
        source = self.doc(fx.scl(
            fx.header(),
            fx.ied("R1", fx.access_point(
                "S1", fx.ldevice("LD", fx.ln0(ln_type="T_LLN0")))),
            fx.ied("R2", fx.access_point(
                "S1", fx.ldevice("LD", fx.ln0(ln_type="T_LLN0")))),
            fx.templates(fx.lnode_type("T_LLN0", "LLN0",
                                       dos=(("Beh", "SPS_1"),)),
                         fx.do_type("SPS_1", cdc="SPS"))))
        target = self.target()
        target.apply_edit(insert_ied(target, source, ["R1", "R2"]))
        self.assertEqual(self.ieds(target), ["R1", "R2"])
        self.assertEqual(self.pool(target),
                         {("LNodeType", "T_LLN0"), ("DOType", "SPS_1")})

    def test_the_named_order_is_the_document_order(self):
        source = self.doc(fx.scl(
            fx.header(), fx.ied("R1"), fx.ied("R2"), fx.ied("R3")))
        target = self.target()
        target.apply_edit(insert_ied(target, source, ["R3", "R1"]))
        self.assertEqual(self.ieds(target), ["R3", "R1"])

    def test_an_ext_ref_naming_a_device_the_target_lacks_is_left_alone(self):
        """4,291 of the corpus's 4,291 `ExtRef@iedName` name an IED of their
        own file, so a binding that dangles after an import dangles only
        because the rest of the source was left behind. Blanking it would
        destroy a subscription the next name in the list may resolve."""
        ext = fx.ext_ref(iedName="ELSEWHERE", ldInst="LD", lnClass="MMXU",
                         doName="TotW", intAddr="IN1")
        source = self.source(ext_refs=(ext,))
        target = self.target()
        target.apply_edit(insert_ied(target, source, ["R1"]))
        self.assertEqual(
            [el.get("iedName") for el in iter_local(target.root, "ExtRef")],
            ["ELSEWHERE"])


class TestInsertRepointsLnType(_Insert):
    """Under `"rename"` the types enter under fresh ids, and a copy that kept
    the source's would name the TARGET's own different type. On the corpus
    that is 114 of one IED's 175 logical nodes."""

    def conflicting_target(self):
        return self.target(fx.templates(
            fx.lnode_type("T_LLN0", "LLN0", dos=(("Beh", "OTHER"),)),
            fx.do_type("OTHER", cdc="SPS")))

    def ln_types(self, doc):
        return [el.get("lnType") for el in iter_local(doc.root, "LN0")
                if el.get("lnType")]

    def test_a_renamed_type_is_followed_into_the_copy(self):
        target, source = self.conflicting_target(), self.source()
        target.apply_edit(insert_ied(target, source, ["R1"],
                                     on_conflict="rename"))
        self.assertEqual(self.ln_types(target), ["T_LLN0_1"])
        self.assertIn(("LNodeType", "T_LLN0_1"), self.pool(target))

    def test_the_target_s_own_type_and_its_users_are_untouched(self):
        target = self.doc(fx.scl(
            fx.header(),
            fx.ied("OLD", fx.access_point(
                "S1", fx.ldevice("LD", fx.ln0(ln_type="T_LLN0")))),
            fx.templates(fx.lnode_type("T_LLN0", "LLN0",
                                       dos=(("Beh", "OTHER"),)),
                         fx.do_type("OTHER", cdc="SPS"))))
        target.apply_edit(insert_ied(target, self.source(), ["R1"],
                                     on_conflict="rename"))
        self.assertEqual(self.ln_types(target), ["T_LLN0", "T_LLN0_1"])

    def test_every_ln_type_in_the_result_resolves(self):
        """The check that matters, and it is a count of the RESULT rather than
        a reading of the edit list -- Q34 §4's lesson."""
        target = self.conflicting_target()
        target.apply_edit(insert_ied(target, self.source(), ["R1"],
                                     on_conflict="rename"))
        pool = {id_ for kind, id_ in self.pool(target)
                if kind == "LNodeType"}
        for element in target.root.iter():
            if strip_ns(element.tag) in ("LN", "LN0", "LNode"):
                if element.get("lnType"):
                    self.assertIn(element.get("lnType"), pool)

    def test_nothing_is_repointed_when_nothing_is_renamed(self):
        target, source = self.target(), self.source()
        target.apply_edit(insert_ied(target, source, ["R1"]))
        self.assertEqual(self.ln_types(target), ["T_LLN0"])

    def test_a_private_type_attribute_is_not_a_template_reference(self):
        """229 of `QPC2_TR1_AL11`'s attributes are ``Private@type``. They are
        a vendor's own vocabulary and this function must not read them as
        `LNodeType` ids."""
        source = self.doc(fx.scl(
            fx.header(),
            fx.ied("R1", '<Private type="T_LLN0">x</Private>'
                   + fx.access_point("S1", fx.ldevice(
                       "LD", fx.ln0(ln_type="T_LLN0")))),
            fx.templates(fx.lnode_type("T_LLN0", "LLN0",
                                       dos=(("Beh", "SPS_1"),)),
                         fx.do_type("SPS_1", cdc="SPS"))))
        target = self.conflicting_target()
        target.apply_edit(insert_ied(target, source, ["R1"],
                                     on_conflict="rename"))
        private = next(iter_local(target.root, "Private"))
        self.assertEqual(private.get("type"), "T_LLN0")
        self.assertEqual(self.ln_types(target), ["T_LLN0_1"])


class TestInsertCommunication(_Insert):
    def test_it_is_off_by_default(self):
        """The reference's `InsertIedOptions` is an optional argument, and
        this package asks for the larger action rather than arriving at it."""
        target, source = self.target(), self.source()
        target.apply_edit(insert_ied(target, source, ["R1"]))
        self.assertEqual(self.subnets(target), [])
        self.assertNotIn("Communication", self.sections(target))

    def test_a_missing_subnetwork_is_created(self):
        """The ordinary case, not the edge: the three corpus exports share not
        one `SubNetwork` name between them in any of the six ordered pairs."""
        target, source = self.target(), self.source()
        target.apply_edit(insert_ied(target, source, ["R1"],
                                     add_communication=True))
        self.assertEqual(self.subnets(target), [("SRCNET", "8-MMS", ["R1"])])

    def test_a_created_subnetwork_carries_name_and_type_and_no_children(self):
        """The source `SubNetwork`'s own `Text`, `BitRate` and `Private`
        describe the SOURCE's network. A `Private` in particular is the vendor
        bookkeeping `sellib` and `siemenslib` read."""
        source = self.doc(fx.scl(
            fx.header(),
            fx.communication(
                '<SubNetwork name="SRCNET" type="8-MMS">'
                "<Text>source net</Text><BitRate unit=\"b/s\">100</BitRate>"
                '<Private type="vendor">keep me out</Private>'
                + fx.connected_ap("R1", "S1") + "</SubNetwork>"),
            fx.ied("R1", fx.access_point("S1")),
            fx.templates()))
        target = self.target()
        target.apply_edit(insert_ied(target, source, ["R1"],
                                     add_communication=True))
        subnet = next(iter_local(target.root, "SubNetwork"))
        self.assertEqual(subnet.attrib, {"name": "SRCNET", "type": "8-MMS"})
        self.assertEqual([strip_ns(c.tag) for c in subnet], ["ConnectedAP"])

    def test_a_matching_subnetwork_receives_the_copy(self):
        target = self.target(fx.communication(
            fx.subnetwork("SRCNET", (fx.connected_ap("OTHER", "S1"),))))
        target.apply_edit(insert_ied(target, self.source(), ["R1"],
                                     add_communication=True))
        self.assertEqual(self.subnets(target),
                         [("SRCNET", "8-MMS", ["OTHER", "R1"])])

    def test_a_matching_but_EMPTY_subnetwork_receives_it_too(self):
        """The regression this phase found by counting the result. An
        `Element` with no children is FALSY in ElementTree, so a lookup
        written as ``have.get(name) or created.get(name)`` silently discards
        an existing empty `SubNetwork` and builds a second one beside it.
        Invisible on the corpus: all 17 of its SubNetworks hold access
        points."""
        target = self.target(fx.communication(fx.subnetwork("SRCNET")))
        target.apply_edit(insert_ied(target, self.source(), ["R1"],
                                     add_communication=True))
        self.assertEqual(self.subnets(target), [("SRCNET", "8-MMS", ["R1"])])

    def test_a_matching_subnetwork_keeps_its_own_type(self):
        """Rewriting `SubNetwork@type` to the source's would re-type every
        other `ConnectedAP` already in it. No corpus pair shares a name, so
        this never fires on real material and is pinned for that reason."""
        target = self.target(fx.communication(
            fx.subnetwork("SRCNET", type_="8-MMS/TCP")))
        target.apply_edit(insert_ied(target, self.source(), ["R1"],
                                     add_communication=True))
        self.assertEqual(self.subnets(target),
                         [("SRCNET", "8-MMS/TCP", ["R1"])])

    def test_every_access_point_comes_not_merely_the_first(self):
        """A device is not one access point: `mixed.scd` puts 12 of its 14
        IEDs in two SubNetworks and `sel.scd` puts `RTAC_1` in ten."""
        source = self.doc(fx.scl(
            fx.header(),
            fx.communication(
                fx.subnetwork("A", (fx.connected_ap("R1", "S1"),)),
                fx.subnetwork("B", (fx.connected_ap("R1", "S2"),))),
            fx.ied("R1", fx.access_point("S1") + fx.access_point("S2")),
            fx.templates()))
        target = self.target()
        target.apply_edit(insert_ied(target, source, ["R1"],
                                     add_communication=True))
        self.assertEqual(self.subnets(target),
                         [("A", "8-MMS", ["R1"]), ("B", "8-MMS", ["R1"])])

    def test_two_ieds_in_one_new_subnetwork_create_it_once(self):
        source = self.doc(fx.scl(
            fx.header(),
            fx.communication(fx.subnetwork(
                "N", (fx.connected_ap("R1", "S1"),
                      fx.connected_ap("R2", "S1")))),
            fx.ied("R1", fx.access_point("S1"))
            + fx.ied("R2", fx.access_point("S1")),
            fx.templates()))
        target = self.target()
        target.apply_edit(insert_ied(target, source, ["R1", "R2"],
                                     add_communication=True))
        self.assertEqual(self.subnets(target), [("N", "8-MMS", ["R1", "R2"])])

    def test_only_the_named_ieds_access_points_are_copied(self):
        source = self.doc(fx.scl(
            fx.header(),
            fx.communication(fx.subnetwork(
                "N", (fx.connected_ap("R1", "S1"),
                      fx.connected_ap("R2", "S1")))),
            fx.ied("R1", fx.access_point("S1"))
            + fx.ied("R2", fx.access_point("S1")),
            fx.templates()))
        target = self.target()
        target.apply_edit(insert_ied(target, source, ["R1"],
                                     add_communication=True))
        self.assertEqual(self.subnets(target), [("N", "8-MMS", ["R1"])])

    def test_the_connected_ap_subtree_comes_whole(self):
        """Q31 §6: the subtree is copied and EDITING its `Address` belongs in
        `address.py`, not here. `IP`, `IP-SUBNET` and the `OSI-*` parameters
        arrive as the source wrote them."""
        source = self.doc(fx.scl(
            fx.header(),
            fx.communication(fx.subnetwork("N", (fx.connected_ap(
                "R1", "S1", body=fx.address(**{"IP": "10.0.0.1",
                                               "IP-SUBNET": "255.255.255.0"})),))),
            fx.ied("R1", fx.access_point("S1")),
            fx.templates()))
        target = self.target()
        target.apply_edit(insert_ied(target, source, ["R1"],
                                     add_communication=True))
        values = {p.get("type"): p.text
                  for p in iter_local(target.root, "P")}
        self.assertEqual(values, {"IP": "10.0.0.1",
                                  "IP-SUBNET": "255.255.255.0"})

    def test_an_orphan_connected_ap_in_the_target_is_refused(self):
        """The target holds a `ConnectedAP` naming a device it does not have.
        Adding a second description of the same access point would make the
        document say two things. No corpus file contains one: all 79 name an
        IED that is present."""
        target = self.target(fx.communication(
            fx.subnetwork("SRCNET", (fx.connected_ap("R1", "S1"),))))
        with self.assertRaises(EditRejected) as cm:
            insert_ied(target, self.source(), ["R1"],
                       add_communication=True)
        self.assertIn("ConnectedAP", str(cm.exception))

    def test_a_source_ied_with_no_access_point_copies_nothing(self):
        source = self.doc(fx.scl(fx.header(), fx.ied("R1"), fx.templates()))
        target = self.target()
        target.apply_edit(insert_ied(target, source, ["R1"],
                                     add_communication=True))
        self.assertEqual(self.subnets(target), [])


class TestInsertPlacement(_Insert):
    """`Communication`, `IED` and `DataTypeTemplates` are in that order in the
    SCL content model, and a target missing two of the three resolves both
    insertion references to "append" -- so the edits have to be built in that
    order or the `Communication` lands after the IED it describes."""

    def assert_order(self, target):
        sections = [s for s in self.sections(target)
                    if s in ("Communication", "IED", "DataTypeTemplates")]
        self.assertEqual(sections, sorted(
            sections, key=("Communication", "IED",
                           "DataTypeTemplates").index))

    def test_a_target_with_none_of_the_three(self):
        target, source = self.target(), self.source()
        target.apply_edit(insert_ied(target, source, ["R1"],
                                     add_communication=True))
        self.assertEqual(self.sections(target),
                         ["Header", "Communication", "IED",
                          "DataTypeTemplates"])

    def test_a_target_with_data_type_templates_only(self):
        target = self.target(fx.templates(fx.lnode_type("OTHER")))
        target.apply_edit(insert_ied(target, self.source(), ["R1"],
                                     add_communication=True))
        self.assert_order(target)

    def test_a_target_with_communication_only(self):
        target = self.target(fx.communication(fx.subnetwork("N")))
        target.apply_edit(insert_ied(target, self.source(), ["R1"],
                                     add_communication=True))
        self.assert_order(target)

    def test_a_target_with_all_three_already(self):
        target = self.target(
            fx.communication(fx.subnetwork("N")), fx.ied("OTHER"),
            fx.templates(fx.lnode_type("OTHER")))
        target.apply_edit(insert_ied(target, self.source(), ["R1"],
                                     add_communication=True))
        self.assert_order(target)
        self.assertEqual(self.ieds(target), ["OTHER", "R1"])

    def test_the_ied_goes_among_the_ieds_not_after_the_templates(self):
        target = self.target(fx.ied("OTHER"),
                             fx.templates(fx.lnode_type("OTHER")))
        target.apply_edit(insert_ied(target, self.source(), ["R1"]))
        self.assertEqual(self.sections(target),
                         ["Header", "IED", "IED", "DataTypeTemplates"])


class TestInsertInvertible(_Insert):
    def test_an_import_inverts_to_the_byte(self):
        target, source = self.target(), self.source()
        before = target.to_bytes()
        undo = target.apply_edit(insert_ied(target, source, ["R1"],
                                            add_communication=True))
        self.assertNotEqual(target.to_bytes(), before)
        target.apply_edit(undo)
        self.assertEqual(target.to_bytes(), before)

    def test_a_renaming_import_inverts_to_the_byte(self):
        target = self.target(fx.templates(
            fx.lnode_type("T_LLN0", "LLN0", dos=(("Beh", "OTHER"),)),
            fx.do_type("OTHER", cdc="SPS")))
        before = target.to_bytes()
        undo = target.apply_edit(insert_ied(
            target, self.source(), ["R1"], on_conflict="rename",
            add_communication=True))
        target.apply_edit(undo)
        self.assertEqual(target.to_bytes(), before)

    def test_an_import_and_a_removal_is_the_file_it_was(self):
        """The two halves of this module meeting: what `insert_ied` writes,
        `remove_ied` takes back out. The templates stay, as A13 decided and
        Q34 §7 confirmed -- so this compares the IED sections, not the file."""
        target, source = self.target(), self.source()
        target.apply_edit(insert_ied(target, source, ["R1"]))
        inserted = next(c for c in target.root if strip_ns(c.tag) == "IED")
        target.apply_edit(remove_ied(target, Remove(inserted)))
        self.assertEqual(self.ieds(target), [])


class TestInsertCorpus(unittest.TestCase):
    """The measurements every decision in `insert_ied` was taken on, and one
    real import of a real device across two vendors."""

    FILES = ("sel.scd", "mixed.scd", "siemens.scd")
    NAMES = ("QPC2_TR1_AL11", "QPC2_TR1_AL12")

    def corpus(self, name):
        path = roundtrip.CORPUS / name
        if not path.is_file():
            self.skipTest("the corpus is not in this distribution")
        return SclDocument.parse(path)

    def ieds(self, doc):
        return [child for child in doc.root if strip_ns(child.tag) == "IED"]

    def named(self, doc, name):
        return next(el for el in self.ieds(doc) if el.get("name") == name)

    def subnets(self, doc):
        out = []
        for child in doc.root:
            if strip_ns(child.tag) == "Communication":
                out.extend(s for s in child if strip_ns(s.tag) == "SubNetwork")
        return out

    def pool(self, doc):
        for child in doc.root:
            if strip_ns(child.tag) == "DataTypeTemplates":
                return [(strip_ns(t.tag), t.get("id")) for t in child]
        return []

    def ln_types(self, element):
        return [el.get("lnType") for el in element.iter()
                if strip_ns(el.tag) in ("LN", "LN0") and el.get("lnType")]

    # -- the measurements the decisions rest on -----------------------------

    def test_no_two_exports_share_a_subnetwork_name(self):
        """Why a missing `SubNetwork` is CREATED rather than refused: in all
        six ordered pairs of the three exports there is not one name in
        common, so refusing would make `add_communication=True` fail on every
        pair of real files there is."""
        by_file = {name: {s.get("name") for s in self.subnets(self.corpus(name))}
                   for name in self.FILES}
        self.assertEqual(
            sorted(by_file["siemens.scd"]), ["Default_subnet"])
        for left in self.FILES:
            for right in self.FILES:
                if left != right:
                    self.assertEqual(by_file[left] & by_file[right], set())

    def test_a_device_is_not_one_access_point(self):
        """Why every `ConnectedAP` is copied and not the first: 79 across the
        corpus, and `mixed.scd` puts 12 of its 14 IEDs in two `SubNetwork`s
        while `sel.scd` puts `RTAC_1` in ten."""
        total = 0
        for name in self.FILES:
            doc = self.corpus(name)
            per = {}
            for subnet in self.subnets(doc):
                for ap in subnet:
                    if strip_ns(ap.tag) != "ConnectedAP":
                        continue
                    total += 1
                    per.setdefault(ap.get("iedName"), set()).add(
                        subnet.get("name"))
            if name == "mixed.scd":
                self.assertEqual(
                    sum(1 for nets in per.values() if len(nets) > 1), 12)
            if name == "sel.scd":
                self.assertEqual(len(per["RTAC_1"]), 10)
        self.assertEqual(total, 79)

    def test_every_ext_ref_names_an_ied_of_its_own_file(self):
        """Why `ExtRef@iedName` is left exactly as the source wrote it. All
        4,291 resolve inside their own document, so a binding that dangles
        after an import dangles only because the rest of the source was left
        behind -- and the next name in the list may be what resolves it."""
        total = 0
        for name in self.FILES:
            doc = self.corpus(name)
            own = {el.get("name") for el in self.ieds(doc)}
            namespace = doc.root.tag[:doc.root.tag.index("}") + 1]
            for element in doc.root.iter(namespace + "ExtRef"):
                value = element.get("iedName")
                if value:
                    total += 1
                    self.assertIn(value, own)
        self.assertEqual(total, 4291)

    def test_the_name_collision_is_real_material(self):
        """Why the collision is refused rather than left unchecked as the
        reference leaves it: `sel.scd` and `siemens.scd` share eight device
        names, so an unchecked insert would produce a document with two IEDs
        of one name."""
        names = {name: {el.get("name") for el in self.ieds(self.corpus(name))}
                 for name in self.FILES}
        self.assertEqual(
            len(names["sel.scd"] & names["siemens.scd"]), 8)
        self.assertEqual(names["sel.scd"] & names["mixed.scd"], set())
        self.assertEqual(names["mixed.scd"] & names["siemens.scd"], set())

    def test_two_ieds_of_one_file_share_all_their_types(self):
        """Why `names` is a LIST. `QPC2_TR1_AL11` and `QPC2_TR1_AL12` use the
        same 82 `LNodeType`, so as two calls the second would plan against a
        target that does not yet hold what the first is inserting."""
        source = self.corpus("siemens.scd")
        first, second = (set(self.ln_types(self.named(source, n)))
                         for n in self.NAMES)
        self.assertEqual(len(first), 82)
        self.assertEqual(first, second)

    # -- the import itself --------------------------------------------------

    def imported(self, **kwargs):
        target, source = self.corpus("mixed.scd"), self.corpus("siemens.scd")
        edits = insert_ied(target, source, list(self.NAMES),
                           on_conflict="rename", **kwargs)
        return target, source, edits

    def test_the_default_policy_refuses_on_forty_ids(self):
        target, source = self.corpus("mixed.scd"), self.corpus("siemens.scd")
        with self.assertRaises(EditRejected) as cm:
            insert_ied(target, source, list(self.NAMES))
        self.assertIn("40 data type(s)", str(cm.exception))

    def test_the_whole_import_is_inserts(self):
        _, _, edits = self.imported(add_communication=True)
        self.assertEqual(len(edits), 364)
        self.assertTrue(all(isinstance(edit, Insert) for edit in edits))

    def test_the_closure_arrives_once_for_both_devices(self):
        """82 `LNodeType` pulling 163 `DOType`, 20 `DAType` and 94 `EnumType`
        -- Q34 §3's closure for one of these devices, and the other adds none
        because it uses the same 82."""
        target, _, edits = self.imported()
        before = Counter(kind for kind, _ in self.pool(target))
        target.apply_edit(edits)
        after = Counter(kind for kind, _ in self.pool(target))
        self.assertEqual(
            {kind: after[kind] - before[kind] for kind in after},
            {"LNodeType": 82, "DOType": 163, "DAType": 20, "EnumType": 94})

    def test_no_id_is_written_twice_into_the_pool(self):
        """Q34 §5's defect, checked by counting the RESULT: a renamed id
        landing on one the same import is writing would leave the pool a type
        short and nothing in the edit list would show it."""
        target, _, edits = self.imported()
        target.apply_edit(edits)
        pool = self.pool(target)
        self.assertEqual(len(pool), len(set(pool)))

    def test_every_ln_type_in_the_result_resolves(self):
        target, _, edits = self.imported()
        target.apply_edit(edits)
        declared = {id_ for kind, id_ in self.pool(target)
                    if kind == "LNodeType"}
        for element in target.root.iter():
            if strip_ns(element.tag) in ("LN", "LN0", "LNode"):
                if element.get("lnType"):
                    self.assertIn(element.get("lnType"), declared)

    def test_the_repointing_is_not_hypothetical(self):
        """228 of the two devices' 350 logical nodes name one of the 40
        conflicting types, so without :func:`_repoint_ln_type` more than half
        the imported instances would name the TARGET's own different type."""
        target, source, edits = self.imported()
        plan = lnode_type_conflicts(
            target, source,
            sorted({t for name in self.NAMES
                    for t in self.ln_types(self.named(source, name))}),
            "rename")
        renamed = {key for key, fresh in plan.ids.items() if key[1] != fresh}
        self.assertEqual(len(renamed), 40)
        target.apply_edit(edits)
        # Against the SOURCE's own values, position by position -- not against
        # a suffix. `mixed.scd` already uses two `_n` suffixes of one id, so
        # Q34 §5's rename lands on `..._3` and a test reading the spelling
        # would miscount the very case that question exists for.
        moved = total = 0
        for name in self.NAMES:
            mine = self.ln_types(self.named(target, name))
            theirs = self.ln_types(self.named(source, name))
            self.assertEqual(len(mine), len(theirs))
            total += len(mine)
            moved += sum(1 for a, b in zip(mine, theirs) if a != b)
        self.assertEqual(total, 350)
        self.assertEqual(moved, 228)

    def test_the_access_points_land_in_a_created_subnetwork(self):
        target, _, edits = self.imported(add_communication=True)
        target.apply_edit(edits)
        made = next(s for s in self.subnets(target)
                    if s.get("name") == "Default_subnet")
        self.assertEqual(made.get("type"), "8-MMS")
        self.assertEqual([ap.get("iedName") for ap in made],
                         list(self.NAMES))

    def test_the_source_document_is_not_touched(self):
        target, source, edits = self.imported(add_communication=True)
        before = source.to_bytes()
        target.apply_edit(edits)
        self.assertEqual(source.to_bytes(), before)

    def test_the_import_inverts_to_the_byte(self):
        target, _, edits = self.imported(add_communication=True)
        before = target.to_bytes()
        target.apply_edit(target.apply_edit(edits))
        self.assertEqual(target.to_bytes(), before)

    def test_the_edited_document_round_trips(self):
        target, _, edits = self.imported(add_communication=True)
        target.apply_edit(edits)
        data = target.to_bytes()
        with tempfile.TemporaryDirectory() as directory:
            path = fx.write(directory, "out.scd", data.decode("utf-8"))
            self.assertEqual(SclDocument.parse(path).to_bytes(), data)

    def test_the_sections_stay_in_schema_order(self):
        target, _, edits = self.imported(add_communication=True)
        target.apply_edit(edits)
        order = [strip_ns(child.tag) for child in target.root]
        self.assertLess(order.index("Communication"), order.index("IED"))
        self.assertLess(order.index("IED"),
                        order.index("DataTypeTemplates"))

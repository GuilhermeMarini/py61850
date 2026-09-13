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

from py61850.scl import (
    EditRejected,
    IED_NAME_ELEMENTS,
    ORPHAN_IED_NAME,
    Remove,
    SclDocument,
    SetAttributes,
    control_block_obj_ref,
    iter_local,
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

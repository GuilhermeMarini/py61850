# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The four edit primitives, their inverses, and the parent map.

**Almost every test here ends the same way: the document is byte-identical to
the file it was parsed from.** That is deliberate, and it is why the fixture
goes through a real file rather than `ET.fromstring`. An edit layer is only
worth having if undoing it truly undoes it, and "the tree looks right" is a
weaker claim than the round-trip guarantee this package already makes: a
document that has been edited and put back must serialise to the same bytes,
down to the order the attributes are written in.

That last clause is not decoration. `ET` stores attributes in a plain dict, so
deleting one and setting it again appends it at the end -- the values are
restored and the FILE IS DIFFERENT. `TestAttributeOrderSurvivesAnUndo` is the
test that caught it, and `SetAttributes`' complete-description rule is what
answers it.

## What is not tested here

Generated edit sequences. That is the next phase, deliberately separate so it
cannot be skipped; this file states the behaviour one primitive at a time,
which is what a generator's failures have to be read against.
"""

import tempfile
import unittest
from xml.etree import ElementTree as ET

from py61850.scl import (
    EditRejected,
    Insert,
    Remove,
    SclDocument,
    SetAttributes,
    SetTextContent,
    iter_local,
)
from tests.unit import scl_fixtures as fx


def _station():
    """One IED with two logical devices, the first holding an `<Inputs>` with
    two ExtRefs: the shape every later phase edits.

    Written out here rather than through the `scl_fixtures` helpers because
    the ExtRef ATTRIBUTE ORDER is what half the tests below assert on, and a
    helper that sorts its keyword arguments would hide it."""
    extrefs = (
        '<ExtRef iedName="REL2" ldInst="LD0" lnClass="LLN0" doName="Pos" '
        'daName="stVal" intAddr="VB001" desc="52a"/>'
        '<ExtRef intAddr="VB002" desc="52b"/>'
    )
    return fx.scl(
        fx.header(),
        fx.ied("REL1", desc="the one under test", body=fx.access_point(body=(
            fx.ldevice("LD0", body=fx.ln0(body=f"<Inputs>{extrefs}</Inputs>"))
            + fx.ldevice("LD1", body=fx.ln0())
        ))),
    )


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.doc = SclDocument.parse(
            fx.write(self._tmp.name, "station.scd", _station()))
        self.original = self.doc.to_bytes()

    # -- helpers ------------------------------------------------------------

    def one(self, local_name):
        return next(iter_local(self.doc.root, local_name))

    def all(self, local_name):
        return list(iter_local(self.doc.root, local_name))

    def assertUnchanged(self):
        """The document is the file it was parsed from, byte for byte."""
        self.assertEqual(self.doc.to_bytes(), self.original)

    def assertChanged(self):
        self.assertNotEqual(self.doc.to_bytes(), self.original)

    def roundTrip(self, edit):
        """Apply, assert something moved, undo, assert nothing did."""
        undo = self.doc.apply_edit(edit)
        self.assertChanged()
        self.doc.apply_edit(undo)
        self.assertUnchanged()
        return undo


class TestInsert(_Base):
    def test_appends_when_the_reference_is_none(self):
        inputs = self.one("Inputs")
        new = ET.Element("ExtRef", {"intAddr": "VB003"})
        self.doc.apply_edit(Insert(inputs, new, None))
        self.assertIs(inputs[-1], new)
        self.assertEqual(len(inputs), 3)

    def test_inserts_before_the_reference(self):
        inputs = self.one("Inputs")
        first = inputs[0]
        new = ET.Element("ExtRef", {"intAddr": "VB000"})
        self.doc.apply_edit(Insert(inputs, new, first))
        self.assertIs(inputs[0], new)
        self.assertIs(inputs[1], first)

    def test_a_new_node_inverts_to_a_remove(self):
        inputs = self.one("Inputs")
        undo = self.roundTrip(Insert(inputs, ET.Element("ExtRef"), None))
        self.assertIsInstance(undo, Remove)

    def test_a_whole_subtree_can_be_inserted_and_taken_back_out(self):
        ln0 = self.one("LN0")
        dataset = ET.fromstring(
            '<DataSet name="DS1"><FCDA ldInst="LD0" doName="Pos"/></DataSet>')
        self.roundTrip(Insert(ln0, dataset, None))

    def test_an_element_already_here_is_moved_and_inverts_to_an_insert(self):
        """`insertBefore` on a DOM detaches first, so an Insert of a node that
        is already in the document is a MOVE. The inverse then has to put it
        back where it was, which a Remove cannot express."""
        inputs = self.one("Inputs")
        first, second = inputs[0], inputs[1]
        undo = self.doc.apply_edit(Insert(inputs, first, None))
        self.assertEqual([el is first for el in inputs], [False, True])
        self.assertIs(inputs[0], second)
        self.assertIsInstance(undo, Insert)
        self.assertIs(undo.reference, second)
        self.doc.apply_edit(undo)
        self.assertUnchanged()

    def test_a_move_to_a_different_parent_inverts(self):
        source = self.all("Inputs")[0]
        target = self.all("LN0")[1]
        self.roundTrip(Insert(target, source[0], None))

    def test_a_moved_element_is_not_left_in_two_places(self):
        inputs = self.one("Inputs")
        target = self.all("LN0")[1]
        moving = inputs[0]
        self.doc.apply_edit(Insert(target, moving, None))
        self.assertEqual(len(inputs), 1)
        self.assertIs(self.doc.parent_of(moving), target)


class TestRemove(_Base):
    def test_removes_and_inverts_to_an_insert_before_the_next_sibling(self):
        inputs = self.one("Inputs")
        first, second = inputs[0], inputs[1]
        undo = self.doc.apply_edit(Remove(first))
        self.assertEqual(len(inputs), 1)
        self.assertIsInstance(undo, Insert)
        self.assertIs(undo.parent, inputs)
        self.assertIs(undo.reference, second)
        self.doc.apply_edit(undo)
        self.assertUnchanged()

    def test_the_last_child_inverts_with_no_reference(self):
        inputs = self.one("Inputs")
        undo = self.doc.apply_edit(Remove(inputs[-1]))
        self.assertIsNone(undo.reference)
        self.doc.apply_edit(undo)
        self.assertUnchanged()

    def test_a_whole_ied_comes_back_exactly(self):
        self.roundTrip(Remove(self.one("IED")))

    def test_a_removed_subtree_leaves_the_parent_map(self):
        inputs = self.one("Inputs")
        extref = inputs[0]
        ld = self.one("LDevice")
        self.doc.apply_edit(Remove(ld))
        self.assertIsNone(self.doc.parent_of(extref))
        # ...and a later edit addressing it is refused rather than applied to
        # a subtree that is no longer part of the file.
        with self.assertRaises(EditRejected):
            self.doc.apply_edit(SetAttributes(extref, {"desc": "x"}))


class TestSetAttributes(_Base):
    def test_updates_in_place_and_inverts(self):
        extref = self.one("ExtRef")
        undo = self.roundTrip(SetAttributes(extref, {"desc": "changed"}))
        self.assertEqual(undo.attributes, {"desc": "52a"})

    def test_adds_and_inverts_with_none(self):
        extref = self.one("ExtRef")
        undo = self.doc.apply_edit(SetAttributes(extref, {"srcCBName": "gcb"}))
        self.assertEqual(extref.get("srcCBName"), "gcb")
        self.assertIsNone(undo.attributes["srcCBName"])
        self.doc.apply_edit(undo)
        self.assertUnchanged()

    def test_deletes_and_inverts_with_the_previous_value(self):
        extref = self.one("ExtRef")
        undo = self.doc.apply_edit(SetAttributes(extref, {"desc": None}))
        self.assertIsNone(extref.get("desc"))
        self.assertEqual(undo.attributes["desc"], "52a")
        self.doc.apply_edit(undo)
        self.assertUnchanged()

    def test_one_edit_can_add_update_and_delete_together(self):
        extref = self.one("ExtRef")
        self.roundTrip(SetAttributes(extref, {
            "iedName": "REL3",        # update
            "desc": None,             # delete
            "srcLDInst": "LD9",       # add
        }))

    def test_unsubscribing_an_extref_inverts(self):
        """The shape the subscription phase will actually use: strip the
        binding attributes, then put them all back."""
        extref = self.one("ExtRef")
        binding = ("iedName", "ldInst", "lnClass", "doName", "daName")
        undo = self.doc.apply_edit(
            SetAttributes(extref, {name: None for name in binding}))
        self.assertEqual(list(extref.attrib), ["intAddr", "desc"])
        self.doc.apply_edit(undo)
        self.assertUnchanged()
        self.assertEqual(list(extref.attrib), list(binding) + ["intAddr", "desc"])


class TestAttributeOrderSurvivesAnUndo(_Base):
    """The reason `SetAttributes` has a complete-description rule at all.

    `ET` keeps attributes in a plain dict, so an attribute deleted from the
    middle and then set again lands at the END. Every value would be right and
    the file would still have changed -- which is exactly the failure the
    round-trip guarantee exists to catch, and which no assertion about values
    can see.
    """

    def test_deleting_from_the_middle_and_undoing_restores_the_order(self):
        extref = self.one("ExtRef")
        before = list(extref.attrib)
        self.assertGreater(before.index("lnClass"), 0)
        undo = self.doc.apply_edit(SetAttributes(extref, {"lnClass": None}))
        self.doc.apply_edit(undo)
        self.assertEqual(list(extref.attrib), before)
        self.assertUnchanged()

    def test_the_inverse_of_a_deletion_describes_every_attribute(self):
        extref = self.one("ExtRef")
        undo = self.doc.apply_edit(SetAttributes(extref, {"lnClass": None}))
        self.assertEqual(list(undo.attributes),
                         ["iedName", "ldInst", "lnClass", "doName", "daName",
                          "intAddr", "desc"])

    def test_an_update_only_edit_keeps_its_inverse_minimal(self):
        extref = self.one("ExtRef")
        undo = self.doc.apply_edit(SetAttributes(extref, {"desc": "x"}))
        self.assertEqual(list(undo.attributes), ["desc"])

    def test_a_complete_description_is_written_in_its_own_order(self):
        extref = self.all("ExtRef")[1]
        self.assertEqual(list(extref.attrib), ["intAddr", "desc"])
        self.doc.apply_edit(SetAttributes(extref, {"desc": "b", "intAddr": "a"}))
        self.assertEqual(list(extref.attrib), ["desc", "intAddr"])

    def test_a_complete_description_inverts_into_the_order_it_found(self):
        """The case the test above stops one line short of.

        Applying a complete description reorders; UNDOING one has to put the
        order back, and the inverse can only do that if it describes the
        element as the element was rather than as the edit was. Found by the
        invertibility property test, on all five of its seeds, within thirty
        edits -- values restored and the file different, which is exactly the
        shape Q13 exists for.
        """
        extref = self.all("ExtRef")[1]
        self.assertEqual(list(extref.attrib), ["intAddr", "desc"])
        undo = self.doc.apply_edit(
            SetAttributes(extref, {"desc": "b", "intAddr": "a"}))
        self.assertEqual(list(undo.attributes), ["intAddr", "desc"])
        self.doc.apply_edit(undo)
        self.assertEqual(list(extref.attrib), ["intAddr", "desc"])
        self.assertUnchanged()

    def test_a_values_only_edit_naming_every_attribute_still_inverts(self):
        """The same rule where it is easiest to miss: nothing is added and
        nothing is deleted, so the key set never changes -- and the edit is
        still a complete description, because it happens to name them all."""
        extref = self.all("ExtRef")[1]
        self.roundTrip(SetAttributes(extref, {"desc": "b", "intAddr": "a"}))

    def test_a_partial_description_leaves_the_order_alone(self):
        extref = self.one("ExtRef")
        before = list(extref.attrib)
        self.doc.apply_edit(SetAttributes(extref, {"desc": "z"}))
        self.assertEqual(list(extref.attrib), before)


class TestSetTextContent(_Base):
    def test_sets_and_inverts(self):
        val = ET.Element("Val")
        inputs = self.one("Inputs")
        self.doc.apply_edit(Insert(inputs, val, None))
        undo = self.doc.apply_edit(SetTextContent(val, "1"))
        self.assertEqual(val.text, "1")
        self.assertIsNone(undo.text)
        self.doc.apply_edit(undo)
        self.assertIsNone(val.text)

    def test_none_and_empty_are_different_and_both_survive(self):
        """`<X />` and `<X></X>` are the same element to a parser and not the
        same bytes, so the primitive has to carry the difference."""
        ln0 = self.all("LN0")[1]
        self.assertIsNone(ln0.text)
        undo = self.doc.apply_edit(SetTextContent(ln0, ""))
        self.assertEqual(ln0.text, "")
        self.assertIn(b"<LN0", self.doc.to_bytes())
        self.doc.apply_edit(undo)
        self.assertIsNone(ln0.text)
        self.assertUnchanged()

    def test_children_are_untouched(self):
        """The DOM's `textContent` setter deletes every child; this sets
        `ET`'s `.text`, which is the text BEFORE the first child. That is the
        whole reason this primitive is invertible."""
        inputs = self.one("Inputs")
        children = list(inputs)
        self.roundTrip(SetTextContent(inputs, "not a child"))
        self.assertEqual(list(inputs), children)


class TestListsOfEdits(_Base):
    def test_a_list_applies_in_order_and_inverts_in_reverse(self):
        inputs = self.one("Inputs")
        a = ET.Element("ExtRef", {"intAddr": "A"})
        b = ET.Element("ExtRef", {"intAddr": "B"})
        undo = self.doc.apply_edit([Insert(inputs, a, None),
                                    Insert(inputs, b, None)])
        self.assertEqual([el.get("intAddr") for el in inputs[-2:]], ["A", "B"])
        self.assertIs(undo[0].node, b)
        self.assertIs(undo[1].node, a)
        self.doc.apply_edit(undo)
        self.assertUnchanged()

    def test_an_edit_may_address_what_an_earlier_edit_inserted(self):
        """A compound edit builds as it goes -- insert a DataSet, then put an
        FCDA inside it. The parent map has to know about the DataSet by the
        time the second edit is validated."""
        ln0 = self.one("LN0")
        dataset = ET.Element("DataSet", {"name": "DS1"})
        fcda = ET.Element("FCDA", {"ldInst": "LD0"})
        self.roundTrip([Insert(ln0, dataset, None),
                        Insert(dataset, fcda, None),
                        SetAttributes(dataset, {"desc": "trip"})])

    def test_lists_nest(self):
        inputs = self.one("Inputs")
        self.roundTrip([
            SetAttributes(inputs[0], {"desc": "outer"}),
            [SetAttributes(inputs[1], {"desc": "inner"}),
             [Remove(inputs[1])]],
        ])

    def test_an_empty_list_changes_nothing(self):
        self.assertEqual(self.doc.apply_edit([]), [])
        self.assertUnchanged()


class TestARejectedEditChangesNothing(_Base):
    def reject(self, edit):
        with self.assertRaises(EditRejected) as caught:
            self.doc.apply_edit(edit)
        self.assertUnchanged()
        return str(caught.exception)

    def test_the_root_cannot_be_removed(self):
        self.assertIn("root", self.reject(Remove(self.doc.root)))

    def test_a_detached_node_cannot_be_removed(self):
        """Idempotence would be convenient and it would cost the invariant: a
        node with no parent has no Insert to invert to, so a no-op here would
        put a hole in the list of inverses the next phase generates over."""
        self.assertIn("not in this document",
                      self.reject(Remove(ET.Element("ExtRef"))))

    def test_an_element_from_another_document_is_refused(self):
        other = SclDocument.parse(
            fx.write(self._tmp.name, "other.scd", _station()))
        self.reject(SetAttributes(next(iter_local(other.root, "ExtRef")),
                                  {"desc": "x"}))

    def test_the_reference_must_be_a_child_of_the_parent(self):
        self.assertIn("not a child", self.reject(
            Insert(self.one("Inputs"), ET.Element("ExtRef"),
                   self.one("LDevice"))))

    def test_an_element_cannot_be_inserted_into_itself(self):
        inputs = self.one("Inputs")
        self.assertIn("itself", self.reject(Insert(inputs, inputs, None)))

    def test_an_element_cannot_be_inserted_into_its_own_descendant(self):
        ld = self.one("LDevice")
        self.assertIn("inside itself",
                      self.reject(Insert(self.one("Inputs"), ld, None)))

    def test_the_root_cannot_be_inserted(self):
        self.assertIn("root", self.reject(
            Insert(self.one("Inputs"), self.doc.root, None)))

    def test_a_prefixed_attribute_name_is_refused(self):
        """`sel:x` would be written out as an attribute literally called
        `sel:x`. A namespaced attribute is `{uri}local` here, as `ET` stores
        it -- and getting that wrong produces a file that looks right."""
        self.assertIn("prefixed", self.reject(
            SetAttributes(self.one("ExtRef"), {"sel:x": "1"})))

    def test_a_namespaced_attribute_in_clark_notation_is_accepted(self):
        extref = self.one("ExtRef")
        self.roundTrip(SetAttributes(extref, {"{http://example.com}x": "1"}))

    def test_a_malformed_namespace_in_an_attribute_name_is_refused(self):
        for name in ("{http://example.com", "{}x", "{http://example.com}"):
            with self.subTest(name=name):
                self.reject(SetAttributes(self.one("ExtRef"), {name: "1"}))

    def test_an_attribute_name_with_a_space_is_refused(self):
        self.reject(SetAttributes(self.one("ExtRef"), {"two words": "1"}))

    def test_an_attribute_value_must_be_a_string_or_none(self):
        self.assertIn("string", self.reject(
            SetAttributes(self.one("ExtRef"), {"desc": 3})))

    def test_text_must_be_a_string_or_none(self):
        self.reject(SetTextContent(self.one("ExtRef"), 3))

    def test_something_that_is_not_an_edit_is_refused(self):
        self.assertIn("not an edit", self.reject("Remove(everything)"))

    def test_a_rejection_part_way_through_a_list_rolls_the_rest_back(self):
        """The whole reason the rollback path exists: half a compound edit is
        a document nobody meant, and it would be published as if it were."""
        inputs = self.one("Inputs")
        new = ET.Element("ExtRef", {"intAddr": "VB009"})
        self.reject([
            SetAttributes(inputs[0], {"desc": "changed"}),
            Insert(inputs, new, None),
            Remove(self.doc.root),
        ])
        self.assertEqual(inputs[0].get("desc"), "52a")
        self.assertEqual(len(inputs), 2)

    def test_a_statically_bad_edit_is_caught_before_anything_applies(self):
        """Not the same guarantee as the rollback above: the whole edit is
        checked for what needs no tree at all, so the ordinary caller mistake
        never reaches the document and never needs backing out."""
        inputs = self.one("Inputs")
        self.reject([SetAttributes(inputs[0], {"desc": "changed"}),
                     SetAttributes(inputs[1], {"bad name": "x"})])


class TestTheParentMap(_Base):
    def test_parent_of_walks_back_to_the_root(self):
        extref = self.one("ExtRef")
        seen = []
        node = extref
        while node is not None:
            seen.append(node.tag.rsplit("}", 1)[-1])
            node = self.doc.parent_of(node)
        self.assertEqual(seen, ["ExtRef", "Inputs", "LN0", "LDevice", "Server",
                                "AccessPoint", "IED", "SCL"])

    def test_the_root_has_no_parent(self):
        self.assertIsNone(self.doc.parent_of(self.doc.root))

    def test_a_foreign_element_has_no_parent(self):
        self.assertIsNone(self.doc.parent_of(ET.Element("ExtRef")))

    def test_it_survives_a_sequence_of_inserts_and_removes(self):
        """The phase's own done-when. Twenty edits that move elements between
        two parents, create subtrees and delete them again -- and at the end
        every element still in the document maps to the element that really
        holds it."""
        inputs = self.one("Inputs")
        other = self.all("LN0")[1]
        created = []
        for i in range(10):
            node = ET.fromstring(
                f'<ExtRef intAddr="VB{i:03d}"><Private type="p"/></ExtRef>')
            created.append(node)
            self.doc.apply_edit(Insert(inputs, node, inputs[0]))
        for node in created[:5]:
            self.doc.apply_edit(Insert(other, node, None))
        for node in created[5:]:
            self.doc.apply_edit(Remove(node))

        expected = {child: el for el in self.doc.root.iter() for child in el}
        for child, parent in expected.items():
            self.assertIs(self.doc.parent_of(child), parent)
        self.assertEqual(len(self.doc._cache["parents"]), len(expected))

    def test_an_inserted_subtree_is_mapped_all_the_way_down(self):
        ln0 = self.one("LN0")
        dataset = ET.fromstring(
            '<DataSet name="DS1"><FCDA ldInst="LD0"><Private/></FCDA></DataSet>')
        undo_insert = self.doc.apply_edit(Insert(ln0, dataset, None))
        private = dataset[0][0]
        self.assertIs(self.doc.parent_of(private), dataset[0])
        # A grandchild of something only just inserted is removable, which it
        # would not be if the map only knew about the subtree's root.
        undo_remove = self.doc.apply_edit(Remove(private))
        self.assertIsNone(self.doc.parent_of(private))
        self.doc.apply_edit(undo_remove)
        self.doc.apply_edit(undo_insert)
        self.assertUnchanged()

    def test_a_tree_changed_behind_its_back_is_recovered_not_believed(self):
        """The map is built on the first edit and maintained by the applier.
        Something that goes round it leaves the map stale -- and a lookup that
        misses rebuilds once and retries, so the cost is a walk rather than a
        wrong answer."""
        inputs = self.one("Inputs")
        self.doc.apply_edit(SetAttributes(inputs[0], {"desc": "x"}))   # builds the map
        smuggled = ET.Element("ExtRef", {"intAddr": "VB099"})
        inputs.append(smuggled)                                        # not an edit
        self.assertNotIn(smuggled, self.doc._cache["parents"])
        self.assertIs(self.doc.parent_of(smuggled), inputs)
        self.doc.apply_edit(Remove(smuggled))
        self.assertEqual(len(inputs), 2)

    def test_it_is_not_built_until_something_is_edited(self):
        doc = SclDocument.parse(
            fx.write(self._tmp.name, "unread.scd", _station()))
        self.assertNotIn("parents", doc._cache)
        doc.to_bytes()
        self.assertNotIn("parents", doc._cache)
        doc.apply_edit(SetAttributes(next(iter_local(doc.root, "ExtRef")),
                                     {"desc": "x"}))
        self.assertIn("parents", doc._cache)


class TestEditsAreValues(_Base):
    def test_two_edits_with_the_same_content_are_equal(self):
        el = self.one("ExtRef")
        self.assertEqual(SetAttributes(el, {"desc": "a"}),
                         SetAttributes(el, {"desc": "a"}))
        self.assertNotEqual(SetAttributes(el, {"desc": "a"}),
                            SetAttributes(el, {"desc": "b"}))

    def test_an_edit_cannot_be_changed_after_it_is_made(self):
        with self.assertRaises(Exception):
            Remove(self.one("ExtRef")).node = self.doc.root

    def test_the_mapping_is_copied_so_a_caller_cannot_reach_back_into_it(self):
        attributes = {"desc": "a"}
        edit = SetAttributes(self.one("ExtRef"), attributes)
        attributes["desc"] = "b"
        self.assertEqual(edit.attributes, {"desc": "a"})

    def test_an_edit_reads_as_what_it_does(self):
        self.assertIn("SetTextContent",
                      repr(SetTextContent(self.one("ExtRef"), "x")))


class TestOnACorpusDocument(unittest.TestCase):
    """The fixtures above are small enough to read. This is the same four
    primitives against a real vendor export, where the parent map is 143,258
    entries and the file is 6.7 MB -- the size at which "it inverts" stops
    being obvious."""

    @classmethod
    def setUpClass(cls):
        import os
        cls.path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "fixtures", "scl", "siemens.scd")

    def setUp(self):
        self.doc = SclDocument.parse(self.path)
        self.original = self.doc.to_bytes()

    def test_removing_an_ied_and_putting_it_back_is_the_same_file(self):
        ied = next(iter_local(self.doc.root, "IED"))
        self.doc.apply_edit(self.doc.apply_edit(Remove(ied)))
        self.assertEqual(self.doc.to_bytes(), self.original)

    def test_a_compound_edit_over_a_real_extref_inverts(self):
        extref = next(iter_local(self.doc.root, "ExtRef"))
        parent = self.doc.parent_of(extref)
        undo = self.doc.apply_edit([
            SetAttributes(extref, {"iedName": None, "desc": "bound here"}),
            Insert(parent, ET.Element("ExtRef", {"intAddr": "VB999"}), extref),
        ])
        self.assertNotEqual(self.doc.to_bytes(), self.original)
        self.doc.apply_edit(undo)
        self.assertEqual(self.doc.to_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()

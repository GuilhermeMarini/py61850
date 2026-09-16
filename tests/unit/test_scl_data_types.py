# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""`py61850.scl.data_types` -- importing a data type from another document,
and removing one.

**This is the first module in the package with a corpus that answers its
central question directly.** The three reference exports were built by three
tools and share 165 type `id`s between them, 141 of which carry different
content -- so the conflict `import_lnode_types` exists to resolve is not a
shape a fixture has to invent, it is what the files do. `TestCorpus` at the
bottom pins those numbers and the import of a real 82-type IED across two
vendors.

The built fixtures answer what the corpus cannot: a document with no
`DataTypeTemplates` at all, a pool that is already unreferenced before the
call, a source that references a type it does not define, and a closure whose
renamed `id` collides with one the same closure is importing.
"""

import tempfile
import unittest

from py61850.scl import (
    DATA_TYPE_TAGS,
    EditRejected,
    Insert,
    Remove,
    SclDocument,
    children_local,
    import_lnode_types,
    lnode_type_conflicts,
    remove_data_type,
    same_data_type,
    strip_ns,
    update_lnode_type,
)
from tests.unit import roundtrip
from tests.unit import scl_fixtures as fx


# A closure two levels deep: LNodeType -> DOType -> (DAType, EnumType), with
# the DAType reaching a second EnumType through a BDA. Every kind of reference
# `_REFERENCES` knows about is exercised by this one document.
def pool(lnode="T_LLN0", do="SPS_1", da="AnalogueValue", enum="Beh",
         deep_enum="Dir", cdc="SPS", extra=()):
    types = [
        fx.lnode_type(lnode, "LLN0", dos=(("Beh", do),)),
        fx.do_type(do, cdc=cdc, das=(
            {"name": "stVal", "bType": "Enum", "type": enum, "fc": "ST"},
            {"name": "mag", "bType": "Struct", "type": da, "fc": "MX"},
        )),
        fx.da_type(da, bdas=(
            {"name": "f", "bType": "FLOAT32"},
            {"name": "dir", "bType": "Enum", "type": deep_enum},
        )),
        fx.enum_type(enum, values=((1, "on"), (2, "blocked"))),
        fx.enum_type(deep_enum, values=((0, "unknown"), (1, "forward"))),
    ]
    return fx.templates(*types, *extra)


def station(body=None, ieds=""):
    return fx.scl(fx.header(), ieds, body if body is not None else pool())


class _Base(unittest.TestCase):
    def doc(self, text=None, name="a.scd"):
        if not hasattr(self, "_dir"):
            self._dir = tempfile.TemporaryDirectory()
            self.addCleanup(self._dir.cleanup)
        return SclDocument.parse(fx.write(self._dir.name, name,
                                          text if text is not None
                                          else station()))

    def section(self, doc):
        return next(iter(children_local(doc.root, "DataTypeTemplates")))

    def ids(self, doc, kind):
        return [el.get("id") for el in children_local(self.section(doc), kind)]

    def kinds(self, doc):
        return [strip_ns(el.tag) for el in self.section(doc)]

    def find(self, doc, kind, id_):
        for el in children_local(self.section(doc), kind):
            if el.get("id") == id_:
                return el
        raise AssertionError(f"no {kind} {id_!r}")


# -- sameness ---------------------------------------------------------------

class TestSameDataType(_Base):
    """Strict: tag, attributes, text and children in order. The module
    docstring's measurement is that ignoring `desc` or `Private` changes the
    answer for none of the corpus's 165 shared ids, so strictness is free --
    and it fails in the harmless direction."""

    def test_a_type_equals_itself_across_two_parses(self):
        one, two = self.doc(name="one.scd"), self.doc(name="two.scd")
        self.assertTrue(same_data_type(self.find(one, "DOType", "SPS_1"),
                                       self.find(two, "DOType", "SPS_1")))

    def test_a_differing_attribute_is_a_different_type(self):
        one = self.doc(name="one.scd")
        two = self.doc(station(pool(cdc="INS")), name="two.scd")
        self.assertFalse(same_data_type(self.find(one, "DOType", "SPS_1"),
                                        self.find(two, "DOType", "SPS_1")))

    def test_desc_is_part_of_it(self):
        """`desc` is documentation and ignoring it is defensible; it is kept
        because the corpus says the choice is free and the permissive reading
        is the one that can silently reuse the wrong type."""
        one = self.doc(name="one.scd")
        two = self.doc(name="two.scd")
        self.find(two, "DOType", "SPS_1").set("desc", "a description")
        self.assertFalse(same_data_type(self.find(one, "DOType", "SPS_1"),
                                        self.find(two, "DOType", "SPS_1")))

    def test_a_private_is_part_of_it(self):
        """The case strictness is actually for: `sellib` and `siemenslib` read
        exactly these blocks, so reusing a type that dropped one would make a
        vendor library wrong about a file it did not write."""
        with_private = self.doc(station(pool(extra=(
            fx.lnode_type("P", "LPHD", body='<Private type="x"/>'),))),
            name="with.scd")
        without = self.doc(station(pool(extra=(fx.lnode_type("P", "LPHD"),))),
                           name="without.scd")
        self.assertFalse(same_data_type(self.find(with_private, "LNodeType", "P"),
                                        self.find(without, "LNodeType", "P")))

    def test_child_order_counts(self):
        """`isEqualNode`'s semantics, kept. Two `DOType` declaring the same
        `DA` in a different sequence are not the same element, and under
        `rename` that costs one duplicated pool entry and nothing else."""
        one = self.doc(name="one.scd")
        two = self.doc(name="two.scd")
        target = self.find(two, "DOType", "SPS_1")
        target[:] = list(target)[::-1]
        self.assertFalse(same_data_type(self.find(one, "DOType", "SPS_1"),
                                        target))

    def test_text_is_compared_stripped(self):
        """These are `xs:normalizedString` and the whitespace around an
        `EnumVal` label is the file's indentation, not the label."""
        one = self.doc(name="one.scd")
        two = self.doc(name="two.scd")
        for val in self.find(two, "EnumType", "Beh"):
            val.text = f"\n   {val.text}  "
        self.assertTrue(same_data_type(self.find(one, "EnumType", "Beh"),
                                       self.find(two, "EnumType", "Beh")))

    def test_a_non_element_is_never_the_same(self):
        one = self.doc()
        self.assertFalse(same_data_type(self.find(one, "DOType", "SPS_1"), None))
        self.assertFalse(same_data_type(None, None))


# -- the plan ---------------------------------------------------------------

class TestConflictsPartition(_Base):
    def test_a_fresh_target_adds_the_whole_closure(self):
        target = self.doc(fx.scl(fx.header()), name="empty.scd")
        source = self.doc(name="src.scd")
        plan = lnode_type_conflicts(target, source, ["T_LLN0"])
        self.assertEqual(len(plan.added), 5)
        self.assertEqual(plan.reused, ())
        self.assertEqual(plan.conflicts, ())
        self.assertEqual(plan.ids[("LNodeType", "T_LLN0")], "T_LLN0")

    def test_an_identical_pool_is_all_reuse_and_no_edit(self):
        target, source = self.doc(name="t.scd"), self.doc(name="s.scd")
        plan = lnode_type_conflicts(target, source, ["T_LLN0"])
        self.assertEqual(len(plan.reused), 5)
        self.assertEqual(plan.added, ())
        self.assertEqual(plan.conflicts, ())
        self.assertEqual(import_lnode_types(target, source, ["T_LLN0"]), [])

    def test_the_closure_is_transitive_and_reaches_both_enum_types(self):
        target = self.doc(fx.scl(fx.header()), name="empty.scd")
        source = self.doc(name="src.scd")
        plan = lnode_type_conflicts(target, source, ["T_LLN0"])
        self.assertEqual(
            sorted(plan.added),
            [("DAType", "AnalogueValue"), ("DOType", "SPS_1"),
             ("EnumType", "Beh"), ("EnumType", "Dir"),
             ("LNodeType", "T_LLN0")])

    def test_a_cycle_in_a_da_type_terminates(self):
        """A `DAType` whose `BDA` names itself is malformed but reachable, and
        the read side stops the same way -- `TemplatePool._attribute`'s
        `seen`."""
        types = fx.templates(
            fx.lnode_type("L", "LLN0", dos=(("Beh", "D"),)),
            fx.do_type("D", das=({"name": "v", "bType": "Struct",
                                  "type": "Cyc"},)),
            fx.da_type("Cyc", bdas=({"name": "again", "bType": "Struct",
                                     "type": "Cyc"},)))
        source = self.doc(fx.scl(fx.header(), types), name="cyc.scd")
        target = self.doc(fx.scl(fx.header()), name="empty.scd")
        plan = lnode_type_conflicts(target, source, ["L"])
        self.assertEqual(len(plan.added), 3)


class TestConflictPolicies(_Base):
    def conflicting(self):
        """A target and a source agreeing on every `id` and disagreeing on the
        content of `SPS_1` -- the corpus's ordinary case in miniature."""
        target = self.doc(name="t.scd")
        source = self.doc(station(pool(cdc="INS")), name="s.scd")
        return target, source

    def test_refuse_is_the_default_and_names_the_ids(self):
        target, source = self.conflicting()
        with self.assertRaises(EditRejected) as caught:
            lnode_type_conflicts(target, source, ["T_LLN0"])
        message = str(caught.exception)
        self.assertIn("DOType 'SPS_1'", message)
        self.assertIn("on_conflict=\"rename\"", message)
        self.assertIn("on_conflict=\"overwrite\"", message)

    def test_a_refusal_writes_nothing(self):
        target, source = self.conflicting()
        before = target.to_bytes()
        with self.assertRaises(EditRejected):
            import_lnode_types(target, source, ["T_LLN0"])
        self.assertEqual(target.to_bytes(), before)

    def test_rename_leaves_the_targets_own_type_alone(self):
        target, source = self.conflicting()
        edits = import_lnode_types(target, source, ["T_LLN0"], "rename")
        target.apply_edit(edits)
        self.assertEqual(self.find(target, "DOType", "SPS_1").get("cdc"), "SPS")
        self.assertEqual(self.find(target, "DOType", "SPS_1_1").get("cdc"),
                         "INS")

    def test_rename_repoints_the_importing_structure(self):
        """The whole point: the copied `LNodeType` must name the type that was
        actually written, not the one it named in the source."""
        target, source = self.conflicting()
        target.apply_edit(
            import_lnode_types(target, source, ["T_LLN0"], "rename"))
        imported = self.find(target, "LNodeType", "T_LLN0_1")
        self.assertEqual([d.get("type") for d in children_local(imported, "DO")],
                         ["SPS_1_1"])

    def test_overwrite_replaces_and_emits_a_removal_first(self):
        target, source = self.conflicting()
        edits = import_lnode_types(target, source, ["T_LLN0"], "overwrite")
        removes = [e for e in edits if isinstance(e, Remove)]
        self.assertEqual(sorted(strip_ns(e.node.tag) for e in removes),
                         ["DOType", "LNodeType"])
        target.apply_edit(edits)
        self.assertEqual(self.find(target, "DOType", "SPS_1").get("cdc"), "INS")
        self.assertEqual(self.ids(target, "DOType"), ["SPS_1"])

    def test_an_unknown_policy_is_refused_rather_than_read_as_refuse(self):
        target, source = self.conflicting()
        with self.assertRaises(EditRejected) as caught:
            lnode_type_conflicts(target, source, ["T_LLN0"], "Overwrite")
        self.assertIn("on_conflict must be one of", str(caught.exception))


class TestPlanRefusals(_Base):
    def test_a_bare_string_is_refused(self):
        """`ids` is a sequence; a string would be read one character at a
        time and refuse with a baffling message about an LNodeType 'T'."""
        target, source = self.doc(name="t.scd"), self.doc(name="s.scd")
        with self.assertRaises(EditRejected) as caught:
            lnode_type_conflicts(target, source, "T_LLN0")
        self.assertIn("sequence", str(caught.exception))

    def test_an_id_the_source_lacks_is_refused(self):
        target, source = self.doc(name="t.scd"), self.doc(name="s.scd")
        with self.assertRaises(EditRejected) as caught:
            lnode_type_conflicts(target, source, ["NOPE"])
        self.assertIn("no LNodeType 'NOPE'", str(caught.exception))

    def test_an_element_where_a_document_belongs_says_so(self):
        target = self.doc()
        with self.assertRaises(EditRejected) as caught:
            lnode_type_conflicts(target, target.root, ["T_LLN0"])
        self.assertIn("SclDocument", str(caught.exception))

    def test_a_source_that_does_not_define_what_it_references_is_refused(self):
        """`templates.py` tolerates this on READ -- a trimmed ICD is real.
        Importing it is different: the target would be unable to resolve the
        type just written into it. No corpus file contains one."""
        types = fx.templates(fx.lnode_type("L", "LLN0", dos=(("Beh", "GONE"),)))
        source = self.doc(fx.scl(fx.header(), types), name="trimmed.scd")
        target = self.doc(fx.scl(fx.header()), name="empty.scd")
        with self.assertRaises(EditRejected) as caught:
            import_lnode_types(target, source, ["L"])
        self.assertIn("DOType 'GONE'", str(caught.exception))

    def test_a_namespace_mismatch_is_refused(self):
        """A copy that kept its own namespace would land in
        `DataTypeTemplates`'s `xs:any` slot rather than among its own kind,
        and merging across editions is not this module's question."""
        source = self.doc(fx.scl(fx.header(), pool(), ns=False), name="none.scd")
        target = self.doc(name="t.scd")
        with self.assertRaises(EditRejected) as caught:
            lnode_type_conflicts(target, source, ["T_LLN0"])
        self.assertIn("namespace", str(caught.exception))


class TestRenameCollisionInsideOneClosure(_Base):
    """Q34 §5 -- the defect the smoke test found, pinned.

    A renamed type must not land on an `id` that ANOTHER type in the same
    closure is importing under its own name. Seeding the allocator from the
    target's pool alone is not enough, because an added type claims its source
    `id` too. The corpus has a real instance:
    `SIPROTEC5_LNType_USER_Universal` renames past `mixed.scd`'s two existing
    suffixes onto `..._3`, which `siemens.scd` separately defines."""

    def documents(self):
        target = fx.templates(
            fx.lnode_type("L", "LLN0", dos=(("Beh", "D"),)),
            fx.lnode_type("L_1", "LPHD", dos=(("Beh", "D"),)),
            fx.do_type("D", cdc="SPS"))
        source = fx.templates(
            fx.lnode_type("L", "LPHD", dos=(("Beh", "D"),)),
            fx.lnode_type("L_1", "LPHD", dos=(("Beh", "D"),)),
            fx.do_type("D", cdc="SPS"))
        return (self.doc(fx.scl(fx.header(), target), name="t.scd"),
                self.doc(fx.scl(fx.header(), source), name="s.scd"))

    def test_the_renamed_id_does_not_collide_with_an_imported_one(self):
        target, source = self.documents()
        plan = lnode_type_conflicts(target, source, ["L", "L_1"], "rename")
        self.assertEqual(len(set(plan.ids.values())), len(plan.ids))

    def test_every_planned_type_reaches_the_document(self):
        """The signature of the defect was an edit list that looked right and
        a pool one type short, which only counting catches."""
        target, source = self.documents()
        before = len(self.ids(target, "LNodeType"))
        plan = lnode_type_conflicts(target, source, ["L", "L_1"], "rename")
        written = len(plan.added) + len(plan.conflicts)
        target.apply_edit(
            import_lnode_types(target, source, ["L", "L_1"], "rename"))
        self.assertEqual(len(self.ids(target, "LNodeType")),
                         before + written)
        self.assertEqual(len(set(self.ids(target, "LNodeType"))),
                         len(self.ids(target, "LNodeType")))


# -- importing --------------------------------------------------------------

class TestImportPlacement(_Base):
    def test_a_document_with_no_section_gets_one_first(self):
        """A document with no `DataTypeTemplates` is valid SCL -- an SSD
        describing a substation and no IEDs is the ordinary case."""
        target = self.doc(fx.scl(fx.header()), name="empty.scd")
        source = self.doc(name="src.scd")
        edits = import_lnode_types(target, source, ["T_LLN0"])
        self.assertIsInstance(edits[0], Insert)
        self.assertEqual(strip_ns(edits[0].node.tag), "DataTypeTemplates")
        target.apply_edit(edits)
        self.assertEqual(len(self.ids(target, "LNodeType")), 1)

    def test_each_type_lands_among_its_own_kind(self):
        """A7's ordering: `DataTypeTemplates` is an `xs:sequence` of
        LNodeType, DOType, DAType, EnumType, and a `DOType` appended at the
        end would load here and fail in DIGSI."""
        target = self.doc(station(fx.templates(
            fx.lnode_type("KEEP", "LPHD"),
            fx.enum_type("KEPT", values=((0, "x"),)))), name="t.scd")
        source = self.doc(name="s.scd")
        target.apply_edit(import_lnode_types(target, source, ["T_LLN0"]))
        kinds = self.kinds(target)
        self.assertEqual(kinds, sorted(kinds, key=DATA_TYPE_TAGS.index))

    def test_the_source_document_is_not_touched(self):
        """Ours COPIES where the reference MOVES -- Q31 §6 took that decision
        for `insert_ied` before this phase existed, because `ElementTree` has
        no `importNode` and A5's `Insert` says a foreign node must not be
        handed over."""
        target = self.doc(fx.scl(fx.header()), name="empty.scd")
        source = self.doc(name="src.scd")
        before = source.to_bytes()
        target.apply_edit(import_lnode_types(target, source, ["T_LLN0"]))
        self.assertEqual(source.to_bytes(), before)
        self.assertEqual(len(self.ids(source, "LNodeType")), 1)

    def test_a_renamed_import_does_not_alter_the_source_ids(self):
        target = self.doc(name="t.scd")
        source = self.doc(station(pool(cdc="INS")), name="s.scd")
        target.apply_edit(
            import_lnode_types(target, source, ["T_LLN0"], "rename"))
        self.assertEqual(self.ids(source, "DOType"), ["SPS_1"])

    def test_two_types_sharing_a_sub_type_insert_it_once(self):
        """The reason `ids` is a sequence rather than one id per call: two
        calls would each read a document that does not yet hold what the other
        is inserting. Q33 §9's hazard, one level up."""
        source = self.doc(station(fx.templates(
            fx.lnode_type("A", "LLN0", dos=(("Beh", "SHARED"),)),
            fx.lnode_type("B", "LPHD", dos=(("Beh", "SHARED"),)),
            fx.do_type("SHARED", cdc="SPS"))), name="s.scd")
        target = self.doc(fx.scl(fx.header()), name="empty.scd")
        target.apply_edit(import_lnode_types(target, source, ["A", "B"]))
        self.assertEqual(self.ids(target, "DOType"), ["SHARED"])

    def test_importing_into_the_same_document_is_a_no_op(self):
        doc = self.doc()
        self.assertEqual(import_lnode_types(doc, doc, ["T_LLN0"]), [])


class TestImportInvertible(_Base):
    def test_an_import_and_its_undo_restore_the_bytes(self):
        target = self.doc(name="t.scd")
        source = self.doc(station(pool(cdc="INS")), name="s.scd")
        before = target.to_bytes()
        undo = target.apply_edit(
            import_lnode_types(target, source, ["T_LLN0"], "rename"))
        self.assertNotEqual(target.to_bytes(), before)
        target.apply_edit(undo)
        self.assertEqual(target.to_bytes(), before)

    def test_an_overwrite_and_its_undo_restore_the_bytes(self):
        target = self.doc(name="t.scd")
        source = self.doc(station(pool(cdc="INS")), name="s.scd")
        before = target.to_bytes()
        undo = target.apply_edit(
            import_lnode_types(target, source, ["T_LLN0"], "overwrite"))
        target.apply_edit(undo)
        self.assertEqual(target.to_bytes(), before)


# -- updating ---------------------------------------------------------------

class TestUpdateLNodeType(_Base):
    def test_an_unchanged_type_produces_no_edit(self):
        target, source = self.doc(name="t.scd"), self.doc(name="s.scd")
        self.assertEqual(update_lnode_type(target, source, "T_LLN0"), [])

    def test_the_replacement_keeps_its_position(self):
        """The section's order is undisturbed, which matters because
        `DataTypeTemplates` is a sequence and a reader diffing the file should
        see one element change rather than one move."""
        target = self.doc(station(fx.templates(
            fx.lnode_type("FIRST", "LPHD"),
            fx.lnode_type("T_LLN0", "LLN0", dos=(("Beh", "D"),)),
            fx.lnode_type("LAST", "LPHD"),
            fx.do_type("D", cdc="SPS"))), name="t.scd")
        source = self.doc(station(fx.templates(
            fx.lnode_type("T_LLN0", "LLN0", dos=(("Beh", "D"), ("Mod", "D"))),
            fx.do_type("D", cdc="SPS"))), name="s.scd")
        target.apply_edit(update_lnode_type(target, source, "T_LLN0"))
        self.assertEqual(self.ids(target, "LNodeType"),
                         ["FIRST", "T_LLN0", "LAST"])
        self.assertEqual(
            [d.get("name") for d in
             children_local(self.find(target, "LNodeType", "T_LLN0"), "DO")],
            ["Beh", "Mod"])

    def test_a_new_sub_type_comes_with_it(self):
        target = self.doc(station(fx.templates(
            fx.lnode_type("T_LLN0", "LLN0", dos=(("Beh", "D"),)),
            fx.do_type("D", cdc="SPS"))), name="t.scd")
        source = self.doc(station(fx.templates(
            fx.lnode_type("T_LLN0", "LLN0", dos=(("Beh", "D"), ("Mod", "NEW"))),
            fx.do_type("D", cdc="SPS"),
            fx.do_type("NEW", cdc="INC"))), name="s.scd")
        target.apply_edit(update_lnode_type(target, source, "T_LLN0"))
        self.assertEqual(sorted(self.ids(target, "DOType")), ["D", "NEW"])

    def test_nothing_is_pruned(self):
        """Q25's precedent: one rule for pruning, in one function. A caller
        who wants the pool tight follows this with `remove_data_type` and says
        so."""
        target = self.doc(station(fx.templates(
            fx.lnode_type("T_LLN0", "LLN0", dos=(("Beh", "OLD"),)),
            fx.do_type("OLD", cdc="SPS"))), name="t.scd")
        source = self.doc(station(fx.templates(
            fx.lnode_type("T_LLN0", "LLN0", dos=(("Beh", "NEW"),)),
            fx.do_type("NEW", cdc="INC"))), name="s.scd")
        target.apply_edit(update_lnode_type(target, source, "T_LLN0"))
        self.assertEqual(sorted(self.ids(target, "DOType")), ["NEW", "OLD"])

    def test_a_type_the_target_lacks_is_imports_business(self):
        target = self.doc(fx.scl(fx.header()), name="empty.scd")
        source = self.doc(name="s.scd")
        with self.assertRaises(EditRejected) as caught:
            update_lnode_type(target, source, "T_LLN0")
        self.assertIn("import_lnode_types", str(caught.exception))

    def test_a_type_the_source_lacks_is_refused(self):
        """The target has it and the source does not, so the target-side
        check passes and the source-side one is what speaks."""
        target = self.doc(station(pool(extra=(fx.lnode_type("ONLY_HERE",
                                                            "LPHD"),))),
                          name="t.scd")
        source = self.doc(name="s.scd")
        with self.assertRaises(EditRejected) as caught:
            update_lnode_type(target, source, "ONLY_HERE")
        self.assertIn("source document carries no", str(caught.exception))

    def test_a_conflicting_sub_type_follows_the_policy(self):
        target = self.doc(name="t.scd")
        source = self.doc(station(pool(lnode="T_LLN0", cdc="INS")),
                          name="s.scd")
        with self.assertRaises(EditRejected):
            update_lnode_type(target, source, "T_LLN0")
        target.apply_edit(update_lnode_type(target, source, "T_LLN0", "rename"))
        self.assertEqual(sorted(self.ids(target, "DOType")),
                         ["SPS_1", "SPS_1_1"])

    def test_it_is_invertible(self):
        target = self.doc(name="t.scd")
        source = self.doc(station(pool(cdc="INS")), name="s.scd")
        before = target.to_bytes()
        undo = target.apply_edit(
            update_lnode_type(target, source, "T_LLN0", "rename"))
        target.apply_edit(undo)
        self.assertEqual(target.to_bytes(), before)


# -- removing ---------------------------------------------------------------

class TestRemoveDataType(_Base):
    def unused(self, **kwargs):
        """The pool with nothing instantiating it, so the instance half of
        "linked" is out of the way and the template half can be tested."""
        return self.doc(station(pool(**kwargs)), name="t.scd")

    def test_the_input_edit_comes_first(self):
        """Q23's rule, for the whole of A9 onward: one `apply_edit` call, one
        history entry, and `edits[0] is edit`."""
        doc = self.unused()
        edit = Remove(self.find(doc, "LNodeType", "T_LLN0"))
        edits = remove_data_type(doc, edit)
        self.assertIs(edits[0], edit)

    def test_removing_an_lnode_type_prunes_its_whole_closure(self):
        doc = self.unused()
        doc.apply_edit(remove_data_type(
            doc, Remove(self.find(doc, "LNodeType", "T_LLN0"))))
        self.assertEqual(list(self.section(doc)), [])

    def test_the_prune_is_transitive(self):
        """Removing the `LNodeType` strands the `DOType`, which strands the
        `DAType`, which strands the second `EnumType` only it reached."""
        doc = self.unused()
        edits = remove_data_type(
            doc, Remove(self.find(doc, "LNodeType", "T_LLN0")))
        self.assertEqual(
            sorted(strip_ns(e.node.tag) + " " + e.node.get("id")
                   for e in edits),
            ["DAType AnalogueValue", "DOType SPS_1", "EnumType Beh",
             "EnumType Dir", "LNodeType T_LLN0"])

    def test_a_type_another_still_uses_is_kept(self):
        doc = self.doc(station(fx.templates(
            fx.lnode_type("A", "LLN0", dos=(("Beh", "SHARED"),)),
            fx.lnode_type("B", "LPHD", dos=(("Beh", "SHARED"),)),
            fx.do_type("SHARED", cdc="SPS"))), name="t.scd")
        doc.apply_edit(remove_data_type(doc, Remove(self.find(doc, "LNodeType", "A"))))
        self.assertEqual(self.ids(doc, "DOType"), ["SHARED"])
        self.assertEqual(self.ids(doc, "LNodeType"), ["B"])

    def test_an_already_unreferenced_type_is_left_alone(self):
        """The reference's wording is *"not to leave NEW unlinked data
        types"*, and the qualifier is kept rather than simplified away. The
        corpus cannot tell the two readings apart, having no unreferenced type
        at all, which is exactly why the wording is followed."""
        doc = self.doc(station(pool(extra=(fx.enum_type("ORPHAN",
                                                        values=((0, "x"),)),))),
                       name="t.scd")
        doc.apply_edit(remove_data_type(
            doc, Remove(self.find(doc, "LNodeType", "T_LLN0"))))
        self.assertEqual(self.ids(doc, "EnumType"), ["ORPHAN"])

    def test_a_type_an_instance_names_is_refused(self):
        doc = self.doc(station(
            pool(), ieds=fx.ied("R1", fx.access_point(
                "S1", fx.ldevice("LD", fx.ln0(ln_type="T_LLN0"))))),
            name="t.scd")
        with self.assertRaises(EditRejected) as caught:
            remove_data_type(doc, Remove(self.find(doc, "LNodeType", "T_LLN0")))
        self.assertIn("LN, LN0 or LNode", str(caught.exception))

    def test_force_removes_it_anyway(self):
        doc = self.doc(station(
            pool(), ieds=fx.ied("R1", fx.access_point(
                "S1", fx.ldevice("LD", fx.ln0(ln_type="T_LLN0"))))),
            name="t.scd")
        doc.apply_edit(remove_data_type(
            doc, Remove(self.find(doc, "LNodeType", "T_LLN0")), force=True))
        self.assertEqual(self.ids(doc, "LNodeType"), [])

    def test_a_type_another_template_names_is_refused(self):
        doc = self.unused()
        with self.assertRaises(EditRejected) as caught:
            remove_data_type(doc, Remove(self.find(doc, "DOType", "SPS_1")))
        self.assertIn("LNodeType 'T_LLN0'", str(caught.exception))

    def test_a_refusal_changes_nothing(self):
        doc = self.unused()
        before = doc.to_bytes()
        with self.assertRaises(EditRejected):
            remove_data_type(doc, Remove(self.find(doc, "DOType", "SPS_1")))
        self.assertEqual(doc.to_bytes(), before)

    def test_something_that_is_not_a_data_type_is_refused(self):
        doc = self.unused()
        with self.assertRaises(EditRejected) as caught:
            remove_data_type(doc, Remove(self.section(doc)))
        self.assertIn("not a data type", str(caught.exception))

    def test_it_takes_a_remove(self):
        doc = self.unused()
        with self.assertRaises(EditRejected) as caught:
            remove_data_type(doc, self.find(doc, "DOType", "SPS_1"))
        self.assertIn("takes a Remove", str(caught.exception))

    def test_it_is_invertible(self):
        doc = self.unused()
        before = doc.to_bytes()
        undo = doc.apply_edit(remove_data_type(
            doc, Remove(self.find(doc, "LNodeType", "T_LLN0"))))
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)


# -- the corpus -------------------------------------------------------------

class TestCorpus(unittest.TestCase):
    """The three exports, and the measurements the module's decisions were
    taken on."""

    FILES = ("sel.scd", "mixed.scd", "siemens.scd")

    def corpus(self, name):
        path = roundtrip.CORPUS / name
        if not path.is_file():
            self.skipTest("the corpus is not in this distribution")
        return SclDocument.parse(path)

    def section(self, doc):
        return next(iter(children_local(doc.root, "DataTypeTemplates")))

    def pool(self, doc):
        return {(strip_ns(el.tag), el.get("id")): el
                for el in self.section(doc)}

    def test_the_pools_are_the_sizes_the_module_docstring_states(self):
        counts = {}
        for name in self.FILES:
            doc = self.corpus(name)
            for kind, _ in self.pool(doc):
                counts[kind] = counts.get(kind, 0) + 1
        self.assertEqual(counts, {"LNodeType": 767, "DOType": 794,
                                  "DAType": 86, "EnumType": 304})

    def test_every_pool_is_exactly_closed(self):
        """0 orphans and 0 dangling references in all three. This is the
        invariant an import must add to and a removal must not break, and it
        is what makes `remove_data_type`'s `force` refuse on all 767."""
        from py61850.scl.data_types import (
            _instance_lnode_types,
            _linked,
            _pool,
        )
        for name in self.FILES:
            doc = self.corpus(name)
            pool = _pool(doc.root)
            linked = _linked(pool, set(), _instance_lnode_types(doc.root))
            with self.subTest(file=name, question="orphans"):
                self.assertEqual(sorted(set(pool) - linked), [])

    def test_the_shared_ids_and_the_disagreement(self):
        """The module docstring's central table. 165 ids are shared between a
        pair of exports and 141 of them carry different content, which is why
        `on_conflict` exists at all."""
        pools = {name: self.pool(self.corpus(name)) for name in self.FILES}
        shared = different = 0
        for i, a in enumerate(self.FILES):
            for b in self.FILES[i + 1:]:
                common = set(pools[a]) & set(pools[b])
                shared += len(common)
                different += sum(1 for key in common
                                 if not same_data_type(pools[a][key],
                                                       pools[b][key]))
        self.assertEqual((shared, different), (165, 141))

    def test_mixed_and_siemens_share_ninety_four_lnode_types(self):
        a, b = self.pool(self.corpus("mixed.scd")), self.pool(self.corpus("siemens.scd"))
        common = [key for key in set(a) & set(b) if key[0] == "LNodeType"]
        self.assertEqual(len(common), 94)
        self.assertEqual(sum(1 for key in common
                             if not same_data_type(a[key], b[key])), 89)

    def ied_types(self, doc, name):
        tag = doc.root.tag[:doc.root.tag.index("}") + 1]
        ied = next(c for c in doc.root
                   if c.tag == tag + "IED" and c.get("name") == name)
        return sorted({el.get("lnType") for el in ied.iter()
                       if strip_ns(el.tag) in ("LN", "LN0")
                       and el.get("lnType")})

    def test_a_real_cross_vendor_import_refuses_by_default(self):
        """`QPC2_TR1_AL11` uses 82 `LNodeType` and 40 of those ids already
        exist in `mixed.scd` carrying different content. Refusing is what the
        default is for: nothing is decided for the caller."""
        target, source = self.corpus("mixed.scd"), self.corpus("siemens.scd")
        ids = self.ied_types(source, "QPC2_TR1_AL11")
        self.assertEqual(len(ids), 82)
        plan = lnode_type_conflicts(target, source, ids, "rename")
        self.assertEqual(len(plan.conflicts), 40)
        with self.assertRaises(EditRejected):
            import_lnode_types(target, source, ids)

    def test_a_real_cross_vendor_import_lands_closed_and_inverts(self):
        """The phase's goal in one assertion: 359 types cross two vendors,
        every reference in the result resolves, and undoing it gives back the
        bytes `mixed.scd` came in as."""
        from py61850.scl.data_types import (
            _instance_lnode_types,
            _linked,
            _pool,
            _referenced,
        )
        target, source = self.corpus("mixed.scd"), self.corpus("siemens.scd")
        ids = self.ied_types(source, "QPC2_TR1_AL11")
        before = target.to_bytes()
        plan = lnode_type_conflicts(target, source, ids, "rename")
        edits = import_lnode_types(target, source, ids, "rename")
        undo = target.apply_edit(edits)

        after = _pool(target.root)
        self.assertEqual(len(after),
                         722 + len(plan.added) + len(plan.conflicts))

        # Every reference in the merged pool resolves. This is the assertion
        # the phase is for: 359 types crossed two vendors and the result is
        # still exactly closed, as all three inputs were.
        dangling = [reference for key, element in after.items()
                    for reference in _referenced(element, key[0])
                    if reference not in after]
        self.assertEqual(dangling, [])

        # The only entries nothing points at are the 82 `LNodeType` that were
        # imported, because no IED was imported with them -- which is
        # precisely what `insert_ied` is the next phase for. Every DOType,
        # DAType and EnumType that came across is referenced.
        linked = _linked(after, set(), _instance_lnode_types(target.root))
        idle = sorted(set(after) - linked)
        self.assertEqual({kind for kind, _ in idle}, {"LNodeType"})
        self.assertEqual(len(idle), 82)

        target.apply_edit(undo)
        self.assertEqual(target.to_bytes(), before)

    def test_every_corpus_lnode_type_is_in_use_so_removal_refuses(self):
        """Without `force` this refuses on all 767, which is the guard working
        and the reason the honest use of the function is after the instances
        are gone."""
        refused = 0
        for name in self.FILES:
            doc = self.corpus(name)
            for (kind, _), element in self.pool(doc).items():
                if kind != "LNodeType":
                    continue
                with self.assertRaises(EditRejected):
                    remove_data_type(doc, Remove(element))
                refused += 1
        self.assertEqual(refused, 767)


if __name__ == "__main__":
    unittest.main()

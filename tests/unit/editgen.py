# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Generating sequences of edits, and saying what went wrong when one of them
does not invert.

The machinery half of the invertibility property test.
`test_scl_edit_invertibility.py` holds what must be true; this holds how a
sequence is produced, how a failure is narrowed down, and how both are
reported. It is the same split `roundtrip.py` and `test_scl_roundtrip.py`
use, for the same reason: the assertions stay readable.

**Nothing here asserts.** Every run returns a :class:`Run` whose ``failure``
is either ``None`` or a :class:`Failure` naming the step; the test decides
what that means. A helper that called ``assertEqual`` itself would hide the
step index behind a traceback in the wrong file.

## Seeds

No `hypothesis`, because `py61850` has no dependencies and that is
load-bearing. A seeded `random.Random` instead, and the seeds are a tuple
written into the test module, so CI runs exactly the same sequences on every
machine. `PY61850_EDIT_SEED` overrides them for an exploratory run --
a number for one specific sequence, or `random` for a clock seed, which is
printed like every other seed and can then be pasted back. It is never the
default: a suite whose input changes per run cannot be bisected.

**Every draw goes through `Random.randrange`**, and nothing here calls
`choice`, `sample` or `shuffle`. Those three have changed implementation
between CPython versions -- a checked-in seed that meant one sequence on 3.11
and another on 3.13 would be a reproducibility claim that is not true. The
Mersenne Twister behind `randrange` has not moved.

## What is generated, and what deliberately is not

The five shapes of `SetAttributes` are the point of the exercise. Q13
(`Roadmap/03-scl-editor/04-open-questions.md`) is the failure that values
alone cannot see -- an attribute deleted from the middle of an element and
restored lands at the END -- so the generator produces, with weights: value
updates, a deletion, an addition, a mixed edit that adds and deletes at once,
and a **complete ordered description that permutes the order**. The last one
exercises the rebuild path that the complete-description rule exists for.

Elements are chosen by walking down from the root through random child
indices rather than by sampling a flat list. That costs O(depth) instead of a
walk of the whole tree, it can never pick an element an earlier edit has
already detached, and the path it walks IS the address the script records.
A 20% chance of stopping at each level means a sequence reaches LN and DAI
depth on a real export, and sometimes lands on an `<IED>` -- removing one and
putting it back is the most useful single edit in the corpus.

**Fresh nodes are built detached and enter the document only through an
`Insert`.** That is not tidiness: A5 documented one asymmetry, which is that
`Insert` reads the parent map to tell a move from a plain insertion and does
NOT rebuild on a miss, because every fresh node is a miss and a rebuild is
98 ms. A generator that attached a node with `ET` and then inserted it
through an edit would be outside the contract, and the failure would be the
harness's rather than the library's. Detached construction is not inside that
contract because a detached node is not in the document at all.

Two things are left out on purpose:

- **Namespaced attribute names** (`{uri}local`). A5 already pins that they
  are accepted, and generating them would drag `ElementTree`'s prefix
  invention into a test that is about ordering.
- **`SetTextContent` on a comment.** Accepted by the primitive -- a comment is
  an `Element` -- but a generated text containing `--` produces a file no
  parser will read, and the phase gains nothing from it. Comments are
  generated, moved and removed as NODES, which is the part that matters.

## Shrinking

A 200-edit sequence that fails says almost nothing; the two edits that had to
happen in that order say everything. So a script is recorded declaratively --
paths of child indices, not live `Element` objects -- which makes it
replayable against a freshly parsed document, and therefore shrinkable.

:func:`shrink` is delta debugging by halving (ddmin): drop a chunk, replay, and
keep the shorter script if it still fails. A candidate whose paths no longer
resolve, or whose edits are now rejected, is not a reproduction and is
discarded. It is bounded by a call budget because each replay on a 7 MB export
costs a parse and two serialisations -- generous on the synthetic document,
small on the corpus.
"""

import os
import random
from collections import namedtuple
from xml.etree import ElementTree as ET

from py61850.scl import (
    EditRejected,
    Insert,
    Remove,
    SclDocument,
    SetAttributes,
    SetTextContent,
)

from tests.unit import roundtrip


# -- the script -------------------------------------------------------------
#
# An operation names its targets by PATH -- a tuple of child indices from the
# root -- rather than by holding an `Element`. That is what lets the same
# script be replayed against a document parsed a second time, which is what
# shrinking needs and what makes a failure reproducible from the printed
# output alone. `namedtuple` so a printed script reads as what it does.

NodeSpec = namedtuple("NodeSpec", "tag attrs text children")
InsertNew = namedtuple("InsertNew", "parent reference spec")
Move = namedtuple("Move", "node parent reference")
RemoveNode = namedtuple("RemoveNode", "node")
SetAttrs = namedtuple("SetAttrs", "element attributes")
SetText = namedtuple("SetText", "element text")

KINDS = ("insert", "move", "remove", "attrs", "text")

_NAME_OF = {InsertNew: "insert", Move: "move", RemoveNode: "remove",
            SetAttrs: "attrs", SetText: "text"}

# `attrs` heaviest: the complete-description rule is what this phase exists to
# stress, and an attribute edit is where every ordering failure so far has
# been. `move` and `remove` lighter because each one reshapes the tree the
# remaining edits address, and a sequence that is mostly removals runs out of
# document.
_KIND_WEIGHTS = (("insert", 25), ("move", 15), ("remove", 15),
                 ("attrs", 30), ("text", 15))

_ATTRIBUTE_SHAPES = (("update", 30), ("delete", 20), ("add", 20),
                     ("mixed", 15), ("reorder", 15))

# The tag of a generated comment node. A real `ET` comment carries the
# `Comment` factory as its tag, which is not a string and does not print; the
# script stores this instead and `_build` turns it back.
COMMENT = "#comment"

_TAGS = ("ExtRef", "DAI", "Val", "Private", "P", "LN", "DataSet", "FCDA")

_ATTR_NAMES = ("name", "desc", "type", "inst", "sAddr", "gen1", "gen2")

# Values that survive a serialise-and-compare only if the escaping is
# symmetric: `&` and `<` are escaped in attribute values, `"` becomes
# `&quot;`, and a newline becomes `&#10;`. An inverse that carried a
# re-escaped or half-escaped string would show up here as a byte difference.
_ATTR_VALUES = ("1", "REL1", "a & b", "<b>", 'q"q', "line\nbreak", "",
                "0", "VB001", "  spaced  ")

# `None` and `""` are different values and not interchangeable: an element
# with no text serialises `<X />`, one with empty text `<X></X>`. Both are in
# the pool because the difference is exactly what a sloppy inverse loses.
_TEXTS = (None, "", "x", "a & b", "<tag>", "  ", "line\nbreak", "0")

_UNCHANGED = object()


# -- drawing ----------------------------------------------------------------

def _weighted(rng, pairs):
    total = sum(w for _, w in pairs)
    r = rng.randrange(total)
    for value, weight in pairs:
        r -= weight
        if r < 0:
            return value
    raise AssertionError("unreachable")


def _permuted(rng, seq):
    """`seq` shuffled, by Fisher-Yates through `randrange`.

    Not `random.shuffle`: see the module docstring on why nothing here draws
    through anything but `randrange`.
    """
    out = list(seq)
    for i in range(len(out) - 1, 0, -1):
        j = rng.randrange(i + 1)
        out[i], out[j] = out[j], out[i]
    return out


def _descend(rng, root, min_depth=0, want_parent=False):
    """``(element, path)`` -- an element of the live tree, and how to find it.

    Walks down through random child indices, stopping with a 1-in-5 chance at
    each level, so the draw is cheap on a 143,000-element export and can never
    name an element an earlier edit detached.

    ``want_parent`` stops before stepping into a comment: a comment has no
    children and cannot be a parent, so a caller that needs somewhere to put
    something must not be handed one. ``min_depth=1`` keeps the root out of
    the draw, for the callers where the root would simply be refused.
    """
    node = root
    path = []
    while len(node):
        if len(path) >= min_depth and rng.randrange(5) == 0:
            break
        child = node[rng.randrange(len(node))]
        if want_parent and not isinstance(child.tag, str):
            break
        path.append(_index_of(node, child))
        node = child
    return node, tuple(path)


def _index_of(parent, child):
    for i, c in enumerate(parent):
        if c is child:
            return i
    raise AssertionError("the child just drawn is not in its parent")


def _spec(rng, parent_tag, depth=0):
    """A fresh node to insert, in the same namespace as the element it goes
    into.

    The namespace matters: SCL declares a default `xmlns` on the root, so an
    element written without one would be a different element to every reader
    that resolves namespaces -- and would make the generator, rather than the
    library, the thing producing a document nobody can load.
    """
    if depth == 0 and rng.randrange(10) == 0:
        return NodeSpec(COMMENT, (), " generated %d " % rng.randrange(1000), ())
    prefix = parent_tag[:parent_tag.index("}") + 1] if parent_tag.startswith("{") else ""
    tag = prefix + _TAGS[rng.randrange(len(_TAGS))]
    attrs = tuple(
        (_ATTR_NAMES[rng.randrange(len(_ATTR_NAMES))],
         _ATTR_VALUES[rng.randrange(len(_ATTR_VALUES))])
        for _ in range(rng.randrange(4)))
    # Deduplicated late, so the draw above stays one shape: a dict keeps the
    # LAST value for a repeated name, which is what `ET` would do anyway.
    attrs = tuple(dict(attrs).items())
    text = _TEXTS[rng.randrange(len(_TEXTS))]
    children = ()
    if depth < 2 and rng.randrange(3) == 0:
        children = tuple(_spec(rng, parent_tag, depth + 1)
                         for _ in range(1 + rng.randrange(2)))
    return NodeSpec(tag, attrs, text, children)


def _attribute_edit(rng, element, is_root):
    """The ``attributes`` mapping for a :class:`SetAttributes`, as a tuple of
    pairs, or ``None`` when this element cannot carry the shape drawn.

    A tuple rather than a dict because **the order of the pairs is part of the
    edit**: a mapping that names every attribute the element has is read as a
    complete ordered description and rebuilds them in that order.

    **The root is the one element whose attribute ORDER an edit cannot move.**
    `_reorder_root_attributes` puts the root's attributes back into the order
    the FILE wrote them on every serialise, because it is the one element
    `ElementTree` reorders -- it writes the namespace declarations it
    generates ahead of every ordinary attribute, and a real file interleaves
    the two. So a reordering of the root's attributes is invisible in the
    output, and generating one would assert a change that cannot happen.
    Values and the key set still reach the file, so every other shape stays.
    """
    items = list(element.attrib.items())
    names = [k for k, _ in items]
    shapes = (tuple(s for s in _ATTRIBUTE_SHAPES if s[0] != "reorder")
              if is_root else _ATTRIBUTE_SHAPES)
    shape = _weighted(rng, shapes)
    spare = [n for n in _ATTR_NAMES if n not in element.attrib]

    if shape == "update":
        if not names:
            return None
        chosen = _permuted(rng, names)[:1 + rng.randrange(min(3, len(names)))]
        pairs = []
        for name in chosen:
            value = _other_value(rng, element.attrib[name])
            if value is not _UNCHANGED:
                pairs.append((name, value))
        return tuple(pairs) or None

    if shape == "delete":
        if not names:
            return None
        return ((names[rng.randrange(len(names))], None),)

    if shape == "add":
        if not spare:
            return None
        chosen = _permuted(rng, spare)[:1 + rng.randrange(min(2, len(spare)))]
        return tuple((n, _ATTR_VALUES[rng.randrange(len(_ATTR_VALUES))])
                     for n in chosen)

    if shape == "mixed":
        if len(names) < 2 or not spare:
            return None
        order = _permuted(rng, names)
        updated = _other_value(rng, element.attrib[order[0]])
        if updated is _UNCHANGED:
            return None
        return ((order[0], updated), (order[1], None),
                (spare[rng.randrange(len(spare))],
                 _ATTR_VALUES[rng.randrange(len(_ATTR_VALUES))]))

    # "reorder": every attribute the element has, same values, different
    # order. The edit that the complete-description rule was written for, and
    # the one whose inverse has to put the order back.
    if len(names) < 2:
        return None
    for _ in range(8):
        shuffled = _permuted(rng, items)
        if [k for k, _ in shuffled] != names:
            return tuple(shuffled)
    return None


def _other_value(rng, current):
    for _ in range(8):
        value = _ATTR_VALUES[rng.randrange(len(_ATTR_VALUES))]
        if value != current:
            return value
    return _UNCHANGED


def _other_text(rng, element):
    """A text that is not the one the element has, and that the SERIALISER can
    tell from it.

    `None` and `""` are different values in the tree -- A5 pins that both
    survive an edit and its inverse -- but `ElementTree` writes an element
    with either as `<X />`, because its serialiser tests the text for truth
    rather than for `None`. So a generated `""` on an element whose text is
    `None` changes the document by every measure except the one this phase
    compares on, and asserting that each edit moved the bytes would be
    asserting something untrue about the library.
    """
    current = element.text
    for _ in range(8):
        text = _TEXTS[rng.randrange(len(_TEXTS))]
        if (text or "") != (current or ""):
            return text
    return _UNCHANGED


def choose(doc, rng):
    """One operation against ``doc`` as it stands, or ``None`` for a draw that
    landed somewhere it cannot be carried out.

    **Nothing that would be refused, and nothing that would change nothing.**
    A rejected edit is the subject of its own property; a no-op would make
    "the document changed" untestable at the step that produced it, and a
    sequence full of them would pass while proving less than it looks.
    """
    root = doc.root
    kind = _weighted(rng, _KIND_WEIGHTS)

    if kind == "insert":
        parent, parent_path = _descend(rng, root, want_parent=True)
        reference = rng.randrange(len(parent) + 1)
        return InsertNew(parent_path,
                         None if reference == len(parent) else reference,
                         _spec(rng, parent.tag))

    if kind == "move":
        node, node_path = _descend(rng, root, min_depth=1)
        parent, parent_path = _descend(rng, root, want_parent=True)
        # `min_depth` cannot be honoured once a sequence of removals has left
        # the root childless: the descent has nowhere to step and hands back
        # the root, which neither primitive accepts.
        if not node_path:
            return None
        if parent_path[:len(node_path)] == node_path:
            return None                 # the node itself, or inside it
        reference = rng.randrange(len(parent) + 1)
        if parent_path == node_path[:-1] and reference in (node_path[-1],
                                                           node_path[-1] + 1):
            # `reference` would be the node itself (refused), or the sibling
            # after it, which puts the node back where it already is.
            return None
        return Move(node_path, parent_path,
                    None if reference == len(parent) else reference)

    if kind == "remove":
        _, node_path = _descend(rng, root, min_depth=1)
        return RemoveNode(node_path) if node_path else None

    element, path = _descend(rng, root, want_parent=True)

    if kind == "attrs":
        attributes = _attribute_edit(rng, element, element is root)
        return None if attributes is None else SetAttrs(path, attributes)

    text = _other_text(rng, element)
    return None if text is _UNCHANGED else SetText(path, text)


# -- turning a script back into edits ---------------------------------------

class Unresolvable(Exception):
    """A path that names nothing in this tree. Only a shrunk candidate can
    raise it: a freshly drawn operation addresses the tree it was drawn from."""


def _resolve(root, path):
    node = root
    for i in path:
        if i >= len(node):
            raise Unresolvable(path)
        node = node[i]
    return node


def _reference(parent, index):
    if index is None:
        return None
    if index >= len(parent):
        raise Unresolvable(index)
    return parent[index]


def _build(spec):
    """A fresh, detached node. Called once per application, so a replay gets
    its own nodes rather than re-inserting the ones a previous run left
    behind."""
    if spec.tag == COMMENT:
        return ET.Comment(spec.text)
    node = ET.Element(spec.tag, dict(spec.attrs))
    node.text = spec.text
    for child in spec.children:
        node.append(_build(child))
    return node


def materialise(doc, op):
    """The edit ``op`` describes, against ``doc`` as it stands now."""
    root = doc.root
    if type(op) is InsertNew:
        parent = _resolve(root, op.parent)
        return Insert(parent, _build(op.spec), _reference(parent, op.reference))
    if type(op) is Move:
        parent = _resolve(root, op.parent)
        return Insert(parent, _resolve(root, op.node),
                      _reference(parent, op.reference))
    if type(op) is RemoveNode:
        return Remove(_resolve(root, op.node))
    if type(op) is SetAttrs:
        return SetAttributes(_resolve(root, op.element), dict(op.attributes))
    return SetTextContent(_resolve(root, op.element), op.text)


# -- what a run produced ----------------------------------------------------

Failure = namedtuple("Failure", "kind step op before after")


class Run:
    """One generated sequence, applied and unwound.

    ``failure`` is ``None`` or the first :class:`Failure`; the test asserts on
    it. ``ops`` is the script, which is what gets shrunk and printed.
    """

    __slots__ = ("document", "seed", "mode", "ops", "edits", "counts",
                 "compares", "failure")

    def __init__(self, document, seed, mode):
        self.document = document
        self.seed = seed
        self.mode = mode
        self.ops = []
        self.edits = []
        self.counts = dict.fromkeys(KINDS, 0)
        self.compares = 0
        self.failure = None


def fingerprint(root):
    """A hash of everything a serialiser would write, without serialising.

    The cheap half of "something moved, and then it came back". A full
    `to_bytes()` on `siemens.scd` is 280 ms and on `sel.scd` 840 ms, so
    asserting a change that way on a corpus document would cost more than the
    rest of the suite; the tag, the attributes IN ORDER, the text and the tail
    of every element answer the same question in a walk.

    It is not a substitute for the byte compare and is never used as one: what
    proves the sequence inverted is `to_bytes()` against the bytes the
    document started as.
    """
    return hash(tuple(
        (el.tag if isinstance(el.tag, str) else COMMENT,
         tuple(el.attrib.items()), el.text, el.tail, len(el))
        for el in root.iter()))


def parent_map_disagreements(doc):
    """Every child whose parent the document reports wrongly.

    **This catches a WRONG entry, not a missing one.** `parent_of` rebuilds
    the map when a lookup misses and then answers correctly, by design -- A5
    chose a 98 ms recovery over a wrong answer -- so an entry the applier
    dropped is repaired by the first sweep that notices it. A map that has
    gone stale in the direction that matters, pointing an element at a parent
    it no longer has, has no such escape and shows up here.
    """
    bad = []
    for element in doc.root.iter():
        for child in element:
            if doc.parent_of(child) is not element:
                bad.append((_tag_of(child), _tag_of(element)))
    return bad


def _tag_of(element):
    tag = element.tag
    if not isinstance(tag, str):
        return COMMENT
    return tag[tag.index("}") + 1:] if tag.startswith("{") else tag


# -- running ----------------------------------------------------------------

def stack_run(doc, seed, count, per_step, document="document"):
    """Generate ``count`` edits, apply them one at a time keeping every
    inverse, then unwind the stack. The shape `pac-ct`'s journal will have.

    ``per_step`` serialises around every single edit -- apply, undo, compare,
    redo, compare -- which localises a failure to the edit that caused it and
    proves the redo lands exactly where the edit did. It costs four
    serialisations per step, so it is for the synthetic document; on a corpus
    export the same 100 edits would be ~56 s.
    """
    rng = random.Random(seed)
    run = Run(document, seed, "stack, every step" if per_step else "stack")
    original = doc.to_bytes()
    run.compares += 1
    current = original
    # Only for the endpoint mode: with `per_step` on, every edit is already
    # proved to have changed the bytes. With it off, something has to say the
    # sequence was not 100 edits that cancelled out, and on a 7 MB export a
    # walk is what that costs instead of a 280 ms serialisation.
    before = None if per_step else fingerprint(doc.root)
    inverses = []

    attempts = 0
    while len(run.ops) < count and attempts < count * 30:
        attempts += 1
        op = choose(doc, rng)
        if op is None:
            continue
        edit = materialise(doc, op)
        undo = doc.apply_edit(edit)
        run.ops.append(op)
        run.edits.append(edit)
        run.counts[_NAME_OF[type(op)]] += 1
        inverses.append(undo)
        if not per_step:
            continue

        applied = doc.to_bytes()
        run.compares += 1
        if applied == current:
            run.failure = Failure("the edit changed nothing",
                                  len(run.ops) - 1, op, current, applied)
            return run
        redo = doc.apply_edit(undo)
        restored = doc.to_bytes()
        run.compares += 1
        if restored != current:
            run.failure = Failure("undoing the edit did not restore the document",
                                  len(run.ops) - 1, op, current, restored)
            return run
        # `redo` is the inverse of the undo, so applying it re-does the edit.
        # The inverse it returns is the one the stack unwinds through: it is
        # computed from the tree as it stands, which is what A5 guarantees.
        inverses[-1] = doc.apply_edit(redo)
        again = doc.to_bytes()
        run.compares += 1
        if again != applied:
            run.failure = Failure("redoing the edit did not land where it did",
                                  len(run.ops) - 1, op, applied, again)
            return run
        current = applied

    if not run.ops:
        run.failure = Failure("no edit was generated", 0, None, b"", b"")
        return run
    if before is not None and fingerprint(doc.root) == before:
        run.failure = Failure("the whole sequence changed nothing",
                              len(run.ops) - 1, None, original, original)
        return run

    for undo in reversed(inverses):
        doc.apply_edit(undo)
    final = doc.to_bytes()
    run.compares += 1
    if final != original:
        run.failure = Failure("the sequence did not invert", len(run.ops) - 1,
                              None, original, final)
    return run


def compound_run(doc, edits, seed, document="document"):
    """Apply ``edits`` as ONE edit -- nested into sub-lists -- and undo it with
    the one inverse that comes back.

    The same sequence through the other path. A list is inverted in reverse
    and a nested list nests its inverse, so a compound edit is one entry in a
    history where the stack above is many; "subscribe this ExtRef" is the case
    that matters.

    ``edits`` must be the objects a :func:`stack_run` produced from THIS
    document, with the document back at the state that run started from: the
    same edits applied from the same state pass through the same intermediate
    states, which is what makes them valid a second time.
    """
    rng = random.Random(seed ^ 0x5C1)
    run = Run(document, seed, "compound, nested")
    original = doc.to_bytes()
    run.compares += 1
    before = fingerprint(doc.root)

    run.edits = _nest(rng, edits)
    undo = doc.apply_edit(run.edits)
    if fingerprint(doc.root) == before:
        run.failure = Failure("the compound edit changed nothing",
                              len(edits) - 1, None, original, original)
        return run
    doc.apply_edit(undo)
    final = doc.to_bytes()
    run.compares += 1
    if final != original:
        run.failure = Failure("the compound edit did not invert",
                              len(edits) - 1, None, original, final)
    return run


def _nest(rng, edits):
    """``edits`` regrouped into sub-lists. A list of edits is itself an edit,
    so this changes the shape of the inverse and nothing else -- which is the
    point of doing it."""
    if len(edits) < 4:
        return list(edits)
    out = []
    i = 0
    while i < len(edits):
        n = 1 + rng.randrange(min(5, len(edits) - i))
        out.append(list(edits[i:i + n]) if n > 1 else edits[i])
        i += n
    return out


# -- narrowing a failure down -----------------------------------------------

class _BudgetSpent(Exception):
    pass


def replay(path, ops, per_step):
    """Apply ``ops`` to a document parsed fresh from ``path`` and unwind.

    ``None`` when the script does not reproduce a failure -- including when it
    no longer fits the tree, which is what a candidate with a chunk taken out
    of the middle usually turns into.
    """
    doc = SclDocument.parse(path)
    original = doc.to_bytes()
    current = original
    inverses = []
    try:
        for i, op in enumerate(ops):
            edit = materialise(doc, op)
            inverses.append(doc.apply_edit(edit))
            if not per_step:
                continue
            applied = doc.to_bytes()
            redo = doc.apply_edit(inverses[-1])
            if doc.to_bytes() != current:
                return Failure("undoing the edit did not restore the document",
                               i, op, current, doc.to_bytes())
            inverses[-1] = doc.apply_edit(redo)
            current = applied
    except (Unresolvable, EditRejected):
        return None
    for undo in reversed(inverses):
        doc.apply_edit(undo)
    final = doc.to_bytes()
    if final != original:
        return Failure("the sequence did not invert", len(ops) - 1, None,
                       original, final)
    return None


def shrink(path, ops, per_step, budget):
    """``(shorter script, replays spent)`` -- delta debugging by halving.

    Drop a chunk, replay, keep the shorter script if it still fails. Stops at
    the budget because a replay costs a parse and two serialisations, which is
    a millisecond on the synthetic document and a second on a 7 MB export.
    """
    spent = [0]

    def fails(candidate):
        if spent[0] >= budget:
            raise _BudgetSpent
        spent[0] += 1
        return replay(path, candidate, per_step) is not None

    n = 2
    try:
        while len(ops) >= 2:
            size = len(ops) // n
            for i in range(n):
                cut_from = i * size
                cut_to = len(ops) if i == n - 1 else (i + 1) * size
                candidate = ops[:cut_from] + ops[cut_to:]
                if candidate and fails(candidate):
                    ops = candidate
                    n = max(n - 1, 2)
                    break
            else:
                if n >= len(ops):
                    break
                n = min(n * 2, len(ops))
    except _BudgetSpent:
        pass
    return ops, spent[0]


# -- reporting --------------------------------------------------------------

def summarise(run):
    """One line per sequence, printed whether it passed or not.

    `unittest` says nothing about a test that passed, and "OK, 480 tests" is
    not evidence that a hundred generated edits inverted byte for byte. The
    kind counts are the other half of it: a sequence that generated no moves
    and no removals would pass while proving much less than it looks.
    """
    kinds = " / ".join(f"{k} {run.counts[k]}" for k in KINDS
                       if run.counts[k] or not run.failure)
    return (f"edit invertibility: {run.document}  seed {run.seed}  "
            f"{len(run.ops)} edits ({kinds})  {run.compares} byte compares  "
            f"{run.mode} -- "
            + ("identical" if run.failure is None else "DIFFERS"))


def explain(run, path, per_step, budget=400):
    """The failure, narrowed and printed: the seed to reproduce it, the
    shortest script that still fails, and where the bytes part company."""
    failure = run.failure
    lines = [f"{failure.kind}, at edit {failure.step} of {len(run.ops)}",
             f"  document : {run.document}",
             f"  mode     : {run.mode}",
             f"  reproduce: PY61850_EDIT_SEED={run.seed}"]

    ops = run.ops[:failure.step + 1]
    shrunk, spent = shrink(path, ops, per_step, budget)
    lines.append(f"  shrunk   : {len(ops)} edits -> {len(shrunk)}, "
                 f"{spent} replays")
    for i, op in enumerate(shrunk):
        lines.append(f"    [{i}] {op!r}")

    # `roundtrip` already knows how to point at the first differing byte with
    # enough context either side to read. A second copy of that would be a
    # second thing to keep right.
    offset, left, right = roundtrip._first_difference(failure.before,
                                                      failure.after)
    lines.append(f"  bytes    : differ at {offset:,} of {len(failure.before):,}")
    lines.append(f"    expected: {left!r}")
    lines.append(f"    got     : {right!r}")
    return "\n".join(lines)


# -- seeds ------------------------------------------------------------------

def seeds(default):
    """The seeds to run: the checked-in tuple, or whatever
    `PY61850_EDIT_SEED` names.

    A number runs that one sequence; `random` draws a clock seed, which is
    printed by :func:`summarise` like any other and can be pasted back to
    reproduce what it found. Neither is the default, because a suite whose
    input changes per run cannot be bisected.
    """
    override = os.environ.get("PY61850_EDIT_SEED")
    if not override:
        return default
    if override == "random":
        return (random.Random().randrange(1 << 30),)
    return (int(override),)

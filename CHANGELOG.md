# Changelog — py61850

What changed in each released version. `ROADMAP.md` is the other half and is
forward-looking: it says what the library can do and what is planned, this says
what moved and when.

**This file starts at 0.5.0.** Releases up to 0.4.0 were written up in their
GitHub release bodies and are still there; they are not reconstructed here,
because a record invented after the fact is worth less than a link to the one
that was written at the time.

---

## Unreleased

**`ruff` and `mypy` are now part of the gate.** Nothing a caller can observe
changes: no name added or removed, no signature moved, no behaviour different.
It is recorded because the standard a library is held to is part of what the
library is — and because the last section below is a defect that was caught
rather than shipped.

`ruff` runs over the whole repository with `E, F, W, I, UP, B` at
`target-version = "py313"`; `mypy` runs over `src/py61850/scl` with the flags
`pac-ct` uses. Both are **development** dependencies, pinned in the workflow and
deliberately absent from the package metadata: `importlib.metadata.requires()`
reports extras with their markers, so a `dev` extra would show up in the list CI
asserts is empty. `pip install py61850` still pulls nothing.

**263 findings closed, the large majority mechanically.** `ruff check --fix`
closed 239 of them (the running total rises to 275, because fixing one finding
exposes another), and 36 were closed by hand. `UP` was affordable only because
0.5.0 raised the floor: 189 of the 214 findings in `src/` are auto-fixable at
3.13, against 16 at the old `>=3.9`. `from __future__ import annotations` is
gone from the 20 modules that carried it, and the `typing.List` / `Optional` /
`Tuple` imports with them.

**26 `mypy` findings, and not one of them was a bug.** That is worth saying
plainly, because the case for a type checker is usually a case about defects and
this is not one. **Fourteen of the 26 are a guard silently encoding an invariant
about another function's return type** — `can_add_report_control` returning
`False` implies a `ConfReportControl` exists, which was true, load-bearing and
written nowhere. The annotations that close them are the point of the exercise.
Eight sites carry `# type: ignore` with the reason on the line above; two of
those are the `try`/`except ImportError` fallback for the optional generated
table, a shape `mypy` cannot express at all.

**Two changes that are not merely cosmetic, both behaviour-preserving:**

- `zip()` now states `strict=` at every call. Three say `strict=True`, where a
  guard on the line above already forces equal lengths and the parameter simply
  writes that guard down. Seven say `strict=False`, where unequal lengths are
  the point — `zip(ranks, ranks[1:])` is the pairwise idiom, and `strict=True`
  would raise on every call.
- `SclDocument.parse` is annotated `-> Self` instead of `-> SclDocument`. It
  ends in `return cls(...)`, so `Self` is the more accurate of the two — and it
  was the one forward reference in the package, which is the next section.

### The future-import deletion was verified for 3.13, not only for 3.14

Deleting `from __future__ import annotations` makes annotations evaluate at
definition time again. **On Python 3.14 it does not**, because PEP 649 makes
them lazy by default — so a tree stripped of those imports can import cleanly,
pass its whole suite on 3.14, and still raise `NameError` at import on the 3.13
floor this package declares.

The check that matters is therefore not that the package imports. Every one of
the **712 annotation scopes** across its 57 modules was evaluated eagerly, and
**one genuine forward reference turned up**: `SclDocument.parse`, whose return
annotation named the class being defined. On 3.13 that is an import-time
`NameError` in the package's central module. It is the `-> Self` change above.

### Verification

1,411 tests, unchanged and all passing, on Python 3.14.6 against the editable
tree. `ruff check .` and `python -m mypy` both clean. `pac-ct`'s 942 tests pass
against this tree, which is the seam nothing in CI checks. `py61850` still has
**zero runtime dependencies**, which CI asserts on every run.

---

## 0.5.0 — 2026-09-15

**`py61850.scl` can now CHANGE an SCL document, not only read one and write it
back.** 0.3.0 gave the object model, 0.4.0 gave the byte-faithful round trip,
and this release puts an edit layer between them: four invertible primitives, a
schema-ordering table that decides where a new element goes, and the IEC 61850-6
rules that make an edit valid rather than merely applied.

The surface grows from **31 names to 132**. Nothing was removed and nothing
changed meaning — see *Compatibility* at the end, where that is measured rather
than asserted.

### The shape of it

Every function returns **a list of edits the caller applies in one go**, and
`apply_edit` returns the edit that undoes it. That is the whole design: an
operation that touches six elements in four sections of the file is one call,
one application and one undo step.

```python
from py61850.scl import SclDocument, Connection, subscribe

doc = SclDocument.parse("station.scd")

sink  = doc.ied("QMA1_MU1").ext_refs()[2]            # an input awaiting a source
block = doc.ied("TR01_2414").control_blocks()[3]     # the GOOSE that publishes it
fcda  = block.logical_node.data_sets[block.dat_set].fcdas[12]

edits = subscribe(doc, [Connection(sink=sink.element,
                                   fcda=fcda.element,
                                   control_block=block.element)])

undo = doc.apply_edit(edits)     # ONE history entry, however many edits it is
doc.write("station.scd")         # byte-faithful everywhere it did not touch
doc.apply_edit(undo)             # and exactly reversible
```

**The edit layer works on `xml.etree` elements, not on the model objects.** The
model is how you find things; `.element` is what you hand to an edit function.
That is deliberate — an edit has to be expressible for a part of the file the
read model does not cover, and the `Substation` section below is exactly that
case.

Depending on what the document already holds, that one `subscribe` call writes
the ExtRef's binding attributes, creates the `Inputs` element if the logical
node has none, and — **when asked for, with `ignore_supervision=False`, which is
not the default** — instantiates the `LGOS`/`LSVS` supervision logical node that
watches the subscribed control block, allocating its `inst` against what the IED
already uses. Whether that is one edit or six, it is one call, one application
and one undo, and undoing it serialises byte-identically to what was there
before. A property test generates edit sequences and asserts exactly that.

It also refuses: the pair above was chosen by the type-restriction guard, and
the first candidate tried was rejected with

```
EditRejected: ExtRef expects SPS.t (Timestamp); SPS.stVal is BOOLEAN
```

**A rejected edit changes nothing.** Every guard raises `EditRejected` before
touching the tree; there is no partially applied state to clean up.

### What was added

101 names, in fourteen modules. By area:

| Area | New names | What it covers |
|---|---:|---|
| `edit` | 5 | `Insert`, `Remove`, `SetAttributes`, `SetTextContent`, `EditRejected` — the primitives, each computing its inverse before it applies |
| `ordering` | 3 | `reference_for`, `may_contain`, `content_model` — schema-ordered insertion, table-driven from the SCL content models |
| `extref` | 12 | `subscribe` / `unsubscribe` / `is_subscribed`, the type-restriction matching behind them, and `fcda_type` |
| `control_block` | 9 | `control_blocks`, `update_dat_set`, `remove_control_block`, `updated_conf_rev`, `path_id`, `control_block_obj_ref` |
| `data_set` | 10 | `can_add_data_set`, create / update / remove, `can_add_fcda`, `remove_fcda`, `max_attributes` |
| `address` | 6 | `create_gse`, `create_smv`, `change_gse_content`, `change_smv_content`, `change_gse_or_smv_address` |
| `report_control` | 7 | `can_add_report_control`, create / update, `max_report_control`, `number_report_control_instances` |
| `sampled_value_control` | 3 | `can_add_sampled_value_control`, create, update |
| `ied` | 5 | `insert_ied`, `remove_ied`, `update_ied` — and the rename fan-out, which is the substance |
| `supervision` | 11 | `can_instantiate_supervision`, `instantiate_supervision`, `remove_supervision`, `max_supervision` |
| `data_types` | 8 | `import_lnode_types`, `update_lnode_type`, `remove_data_type`, `same_data_type`, `lnode_type_conflicts` |
| `substation` | 10 | `update_substation`, `update_voltage_level`, `update_bay`, `remove_process_element`, `prune_lnode_specification` |
| `generator` | 11 | `next_mac_address`, `next_app_id`, `next_ln_inst`, `unique_element_name` and the ranges they allocate from |
| `controls` | 1 | `CONTROL_BLOCK_TAGS` |

Three of those are worth naming individually, because they are the ones that do
more than their name suggests:

- **`update_ied` renames across the whole document, not just the `IED`.**
  Renaming an IED has to follow every `ExtRef@iedName`, every concatenated
  object reference that embeds it, and the control-block and supervision
  references that name it — six element names in all, which is what the schema
  requires rather than what a search-and-replace would find. `remove_ied`
  cleans up the subscriptions and supervisions pointing at what it removes.
- **`import_lnode_types` merges type templates between documents**, with a
  three-way conflict policy, because 165 type ids are shared between a pair of
  the three reference exports and **141 of them carry different content**. Reuse
  is decided on the type's whole closure, not on the element that names it.
- **`remove_control_block` sweeps by the block**, taking the `GSE` or `SMV`
  address with it and the supervisions that watch it.

### Where this differs from OpenSCD

`open-scd-core` and `scl-lib` are this layer's reference, and the comparison is
behavioural: what theirs does against what ours does. Most divergences are
Python idiom or the fact that this document is an `xml.etree` tree on a server
rather than a DOM in a browser. These are the ones a caller actually meets.

- **Every function returns everything you should apply, input edit first.** The
  reference is not uniform here — one function returns the array including the
  edit you passed it, another returns only the extra edit, a third returns a
  plural array again — so a caller has to remember, per function, whether to
  also apply its own input. Ours has one rule. The cost is a return type that
  disagrees with the reference's for some functions, and the benefit is that
  applying a fragment on its own and leaving the document half-corrected is not
  a mistake you can make.
- **Refusals where the reference guesses.** Subscribing refuses a connection it
  cannot supervise; importing a type refuses an id collision rather than picking
  a winner; inserting an IED refuses a name the target already holds. Each of
  those is a place the reference proceeds. The reasoning is the same every
  time: this library produces files that go back into DIGSI and SEL Architect,
  and a wrong answer that loads is worse than a refusal that does not.
- **Three named outcomes replace a boolean** where an import can reuse, rename
  or conflict, because two of the three are success and a boolean cannot say
  which.
- **`fcda_type` keeps its name**, where the reference calls the same function
  `fcdaBaseTypes`. It was already in 0.4.0's public surface and this is a MINOR
  release; renaming a published name is not available, and the existing name is
  the better one anyway — it returns the same `TypeRestriction` that
  `ext_ref_type_restrictions` returns, so the two read as the pair they are.
- **Allocated names are `new<Tag>_01`**, matching the reference's documented
  behaviour. Ours briefly produced `DataSet`/`DataSet_1` and that was corrected
  before this tag.
- **`prune_lnode_specification` is not called `updateLnType`**, because the
  reference's name for it disagrees with its own doc comment, its return type
  and its directory: it updates no `lnType`, it removes an `LNode`'s
  specification children, and those children live in IEC TR 61850-6-100's
  namespace inside a `Private` rather than in 61850-6 at all.
- **`unique_element_name` is public**, where the reference does not export it.

### What this release does not implement

Stated because overstating it is the one thing a release note can get wrong that
nobody notices for a year.

- **Nothing renames a `GSEControl`.** The reference exports `createGSEControl`
  and `updateGSEControl`; there is no Python equivalent. `ReportControl` and
  `SampledValueControl` have their full create/update pair, `GSEControl` does
  not. This is a gap the work found in itself and did not close.
- **No IEC namespace (NSD) data ships**, so neither `nsdToJson` nor anything
  built on it has an equivalent. This is a licensing posture, not an oversight.
  It was measured rather than assumed: `pDO` resolves from the document for
  **0 of 1,244** ExtRefs in one reference export, so the data genuinely is not
  in the file.
- **No plugin adapters.** `lNodeTypeToSelection` shapes a type for an
  `oscd-tree-grid` widget; the structure it walks is already reachable through
  `TemplatePool`.
- **No `checkPermission`.** Authorisation belongs to whatever is driving the
  library.
- **`Substation` and `Log` still have no READ MODEL**, and the distinction from
  the edit layer above is real. `update_substation`, `update_voltage_level`,
  `update_bay` and `remove_process_element` work on the tree directly and are
  tested against fixtures built by hand. There is no `doc.substation()` object
  tree, because **all twenty-three Substation-section element names appear zero
  times in all three reference exports**, in every namespace — a test asserts
  that emptiness by scanning the bytes. Building a read model with nothing to
  check it against is how a reader acquires confident wrong answers.

### Python 3.13 is the new floor

`requires-python` moves from `>=3.9` to `>=3.13`. **Two reasons, and they are
not the same kind of reason:**

- **3.9 and 3.10 are dead weight.** 3.9 reached end of life in October 2025 and
  3.10 does so in October 2026. That half is a date.
- **3.11 and 3.12 are dropped for breadth, not for age.** Both are still
  supported upstream. Six interpreters is more compatibility surface than a
  library whose consumers all move together has any reason to carry, and one
  fewer axis on the matrix is one fewer place a difference can hide. That half
  is a judgement, and it is recorded as one.

**This cannot break code that already runs.** A floor is enforced by the
resolver, not by the interpreter: `pip install py61850` on 3.12 is served 0.4.0
and never a failure. That is why it travels in a MINOR release alongside a
strictly additive API, and the distinction is the one that matters — removing a
name breaks a program, raising a floor declines to hand one a newer library.

Nothing in the source branched on the interpreter (`sys.version_info` appears
nowhere in `src/py61850`), so the bump deletes no logic. The CI matrix narrows
from six versions to two; Windows still covers both ends of the supported range,
which is now 3.13 and 3.14.

### Compatibility

**Strictly additive, measured one name at a time.** All 31 names of 0.4.0 were
compared against this tree at the level of kind, module, MRO and the signature of
every public member. The entire difference is four additions: `ExtRef` and
`FCDA` each gained an `element` attribute, and `SclDocument` gained `apply_edit`
and `parent_of`. No name was removed, no signature changed, no class lost a
member and nothing moved module.

One consequence worth stating for anyone holding model objects across an edit:
**`apply_edit` invalidates the lazy caches by ancestry, and a model object you
already hold is not updated.** Editing inside one IED forgets that IED and leaves
the other 57 of a station warm; re-reading the edited one costs about 41 ms
against 3 ms warm on a 22 MB export.

### Verification

1,411 tests, the round trip held against 44 MB of real station exports from
three vendors, and the edit-invertibility property test run over generated
sequences. `py61850` still has **zero runtime dependencies**, which CI asserts
on every run.

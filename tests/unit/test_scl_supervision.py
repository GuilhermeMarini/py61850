# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""`py61850.scl.supervision` -- instantiating and removing `LGOS`/`LSVS`.

**This phase has more corpus material than any since A8, and it decides
things the fixtures cannot.** 600 supervision logical nodes across the three
station exports, 51 `SupSubscription` declarations, one IED already over its
own limit, and two vendors writing an idle supervision node two different
ways. `TestCorpus` at the bottom pins each measurement the module's decisions
rest on, so a decision cannot quietly stop being true.

The built fixtures carry the other half: the branches no vendor happens to
write -- a `DAI` with no `Val`, an `LN` with no `DOI` -- and the refusals,
which the corpus cannot supply because no file in it is a document somebody
was in the middle of editing.
"""

import tempfile
import unittest

from py61850.scl import (
    LN_INST_RANGE,
    Connection,
    EditRejected,
    Insert,
    Remove,
    SclDocument,
    SetTextContent,
    Supervision,
    can_instantiate_supervision,
    can_remove_supervision,
    control_block_obj_ref,
    find_control_block_subscription,
    instantiate_supervision,
    is_src_ref_editable,
    iter_local,
    max_supervision,
    remove_control_block,
    remove_data_set,
    remove_fcda,
    remove_supervision,
    source_control_block,
    subscribe,
    supervision_ln_class,
    unsubscribe,
)
from py61850.scl.supervision import (
    _bound_supervisions,
    _first_free,
    _ied_of,
    _supervised_reference,
    _supervision_lns,
    _watching,
)
from tests.unit import roundtrip
from tests.unit import scl_fixtures as fx

PUB_CB_REF = "PUBLD/LLN0.GC1"
PUB_SV_REF = "PUBLD/LLN0.SV1"


def publisher(name="PUB", blocks=None, reports=False, members=1):
    """One IED publishing a GOOSE block and a sampled-value block.

    ``blocks`` names extra `GSEControl` elements, which the batch tests need:
    two supervisions planned in one call is only expressible with two blocks
    to supervise. ``reports`` adds a `ReportControl`, which nothing supervises
    and which therefore has to produce nothing rather than an error.
    """
    # A second member exists only so that `remove_fcda` has something to
    # remove without emptying the dataset, which it refuses to do (Q27).
    fcdas = [fx.fcda("LD", "MMXU", "TotW", "MX")][:1] + (
        [fx.fcda("LD", "MMXU", "TotVAr", "MX")] if members > 1 else [])
    body = fx.dataset("DS1", tuple(fcdas))
    body += fx.gse_control("GC1", dat_set="DS1", app_id="PUB_APP")
    for extra in (blocks or ()):
        body += fx.gse_control(extra, dat_set="DS1", app_id=f"PUB_{extra}")
    if reports:
        body += fx.report_control("RC1", dat_set="DS1")
    body += fx.smv_control("SV1", dat_set="DS1")
    return fx.ied(name, fx.access_point(
        "S1", fx.ldevice("LD", fx.ln0(body=body))))


def subscribed(cb_name="GC1", int_addr="in1", do_name="TotW"):
    """An `ExtRef` bound to one of :func:`publisher`'s GOOSE blocks.

    `intAddr` is written so that unsubscribing BLANKS the element and keeps
    it, which makes the supervision assertions readable -- the node is still
    findable afterwards. Which of the two shapes an `ExtRef` has changes
    nothing about supervision: what the expansion reads is `srcCBName`, and it
    reads it before anything is applied.
    """
    return fx.ext_ref(iedName="PUB", ldInst="LD", lnClass="MMXU",
                      doName=do_name, daName="mag.f", intAddr=int_addr,
                      serviceType="GOOSE", srcCBName=cb_name,
                      srcLDInst="LD", srcLNClass="LLN0")


def unbound(int_addr="free1"):
    """An `ExtRef` carrying only an internal address -- an input the IED
    published and nothing is connected to yet. It is what `subscribe` binds,
    and it declares no `p*` restriction, so nothing here is testing A8's
    type checks by accident."""
    return fx.ext_ref(intAddr=int_addr)


def subscriber(name="SUB", nodes=(), services=None, ld_inst="CFG",
               extra_ldevices="", inputs=()):
    """One IED whose `CFG` logical device holds the supervision nodes given.

    ``inputs`` are `ExtRef` elements for the `LN0`'s `Inputs`, which the
    wiring tests need and the A14 tests did not: supervision is written and
    removed because of subscriptions, and until this phase nothing here had
    any.
    """
    body = fx.inputs(*inputs) if inputs else ""
    inner = fx.ldevice(ld_inst, fx.ln0(body=body) + "".join(nodes)) \
        + extra_ldevices
    return fx.ied(name, fx.access_point("S1", inner),
                  **({} if services is None else {}))


def station_text(*ieds, **kwargs):
    """An SCL document holding `ieds` and the supervision type declarations.

    ``val_kind`` and ``val_import`` go on the `DOType`'s `DA`, which is where
    a real file writes them -- no corpus `DAI` writes `valKind` at all.
    """
    val_kind = kwargs.pop("val_kind", "RO")
    val_import = kwargs.pop("val_import", "true")
    assert not kwargs, kwargs
    types = []
    for ln_class in ("LGOS", "LSVS"):
        types.extend(fx.supervision_types(ln_class, val_kind=val_kind,
                                          val_import=val_import))
    return fx.scl(fx.header(), *(list(ieds) + [fx.templates(*types)]))


def with_services(name, nodes, entries, inputs=()):
    """A subscriber IED carrying a `Services` section.

    `Services` goes after `AccessPoint` in `tIED`'s sequence, which is why it
    is spelled here rather than passed to :func:`subscriber`.
    """
    body = fx.inputs(*inputs) if inputs else ""
    return fx.ied(name,
                  fx.access_point("S1", fx.ldevice(
                      "CFG", fx.ln0(body=body) + "".join(nodes)))
                  + fx.services(*entries))


def find(doc, local_name, **attrs):
    for element in iter_local(doc.root, local_name):
        if all(element.get(k) == v for k, v in attrs.items()):
            return element
    raise AssertionError(f"no {local_name} with {attrs}")


def ied_of(doc, name):
    return find(doc, "IED", name=name)


class DocumentCase(unittest.TestCase):
    """A temporary directory per test, because :meth:`SclDocument.parse` takes
    a path -- the same shape `test_scl_data_set` and `test_scl_ied` use."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._written = 0

    def station(self, *ieds, **kwargs):
        self._written += 1
        return SclDocument.parse(fx.write(
            self._tmp.name, f"station{self._written}.scd",
            station_text(*ieds, **kwargs)))


class SupervisionCase(DocumentCase):
    """A publisher, a subscriber with one free `LGOS`, and the types."""

    def setUp(self):
        super().setUp()
        self.doc = self.station(publisher(),
                                subscriber(nodes=(fx.supervision(inst="1"),)))
        self.pub = ied_of(self.doc, "PUB")
        self.sub = ied_of(self.doc, "SUB")
        self.block = find(self.doc, "GSEControl", name="GC1")
        self.sup = Supervision(subscriber=self.sub, control_block=self.block)

    def node(self, inst="1", ln_class="LGOS"):
        for element in _supervision_lns(self.doc, self.sub, ln_class):
            if element.get("inst") == inst:
                return element
        raise AssertionError(f"no {ln_class}{inst}")


# -- what supervises what ---------------------------------------------------

class TestSupervisionLnClass(DocumentCase):

    def setUp(self):
        super().setUp()
        self.doc = self.station(publisher(), subscriber())

    def test_a_goose_block_is_supervised_by_lgos(self):
        block = find(self.doc, "GSEControl", name="GC1")
        self.assertEqual(supervision_ln_class(block), "LGOS")

    def test_a_sampled_value_block_is_supervised_by_lsvs(self):
        block = find(self.doc, "SampledValueControl", name="SV1")
        self.assertEqual(supervision_ln_class(block), "LSVS")

    def test_nothing_supervises_a_report_control(self):
        """61850-7-4 defines no report equivalent of `LGOS`, which is also
        why `update_report_control`'s own `ignore_supervision` has nothing to
        do in either position."""
        doc = self.station(fx.ied("R", fx.access_point("S1", fx.ldevice(
            "LD", fx.ln0(body=fx.report_control("RC1"))))))
        self.assertIsNone(
            supervision_ln_class(find(doc, "ReportControl", name="RC1")))

    def test_it_answers_none_for_anything_that_is_not_a_control_block(self):
        self.assertIsNone(supervision_ln_class(self.doc.root))
        self.assertIsNone(supervision_ln_class("GSEControl"))
        self.assertIsNone(supervision_ln_class(None))


# -- may a tool write the value ---------------------------------------------

class TestIsSrcRefEditable(DocumentCase):
    """The `valKind`/`valImport` question, which is decided in the TEMPLATES.

    Reading the instance alone gets the wrong answer on every corpus file:
    no `DAI` in any of the three writes `valKind`, and the `DOType` behind
    them writes it 443 times.
    """

    def node(self, doc):
        return _supervision_lns(doc, ied_of(doc, "SUB"), "LGOS")[0]

    def build(self, **kwargs):
        doc = self.station(publisher(), subscriber(nodes=(fx.supervision(),)),
                      **kwargs)
        return doc, self.node(doc)

    def test_ro_and_val_import_true_is_editable(self):
        doc, node = self.build(val_kind="RO", val_import="true")
        self.assertTrue(is_src_ref_editable(doc, node))

    def test_conf_is_editable_too(self):
        doc, node = self.build(val_kind="Conf", val_import="true")
        self.assertTrue(is_src_ref_editable(doc, node))

    def test_an_explicit_refusal_is_honoured(self):
        doc, node = self.build(val_kind="RO", val_import="false")
        self.assertFalse(is_src_ref_editable(doc, node))

    def test_a_val_kind_outside_ro_and_conf_refuses(self):
        doc, node = self.build(val_kind="Set", val_import="true")
        self.assertFalse(is_src_ref_editable(doc, node))

    def test_silence_is_permission_and_that_is_the_divergence(self):
        """**The one place this module knowingly departs from the
        reference.** Applying the schema defaults -- `valKind="Set"`,
        `valImport="false"` -- would refuse 194 of the corpus's 600
        supervision nodes, and not one of those 194 carries an explicit
        refusal: 157 declare neither attribute at either level and 37 declare
        `valKind="RO"` with `valImport` absent. Among them are all 24 of
        `sel.scd`'s free slots. A restriction nobody wrote is not a
        restriction, which is A8's rule for `pDO` one step further on."""
        doc, node = self.build(val_kind=None, val_import=None)
        self.assertTrue(is_src_ref_editable(doc, node))

    def test_an_instance_declaration_overrides_the_type(self):
        """61850-6's own precedence: the `DAI` wins over the `DA`."""
        node_xml = fx.ln("LGOS", inst="1", ln_type="T_LGOS", body=fx.doi(
            "GoCBRef",
            '<DAI name="setSrcRef" valImport="false"><Val></Val></DAI>'))
        doc = self.station(publisher(), subscriber(nodes=(node_xml,)),
                      val_kind="RO", val_import="true")
        self.assertFalse(is_src_ref_editable(doc, self.node(doc)))

    def test_it_answers_false_for_anything_that_is_not_a_supervision_node(self):
        doc = self.station(publisher(), subscriber(nodes=(fx.supervision(),)))
        self.assertFalse(is_src_ref_editable(doc, doc.root))


# -- the Services limit -----------------------------------------------------

class TestMaxSupervision(DocumentCase):

    def build(self, entries):
        doc = self.station(publisher(),
                      with_services("SUB", (fx.supervision(),), entries))
        return doc, ied_of(doc, "SUB")

    def test_it_reads_both_limits_and_the_scope(self):
        doc, ied = self.build((fx.sup_subscription("16", "60"),))
        limit = max_supervision(doc, ied)
        self.assertEqual((limit.max_go, limit.max_sv, limit.scope),
                         (16, 60, "IED"))

    def test_max_sv_zero_is_a_real_limit_and_not_an_absent_one(self):
        """43 of the 51 corpus IEDs that declare a `SupSubscription` declare
        `maxSv="0"`: most of these devices support no sampled-value
        supervision at all."""
        doc, ied = self.build((fx.sup_subscription("16", "0"),))
        self.assertEqual(max_supervision(doc, ied).max_sv, 0)

    def test_an_absent_attribute_is_none_rather_than_a_sentinel(self):
        """Q29's argument, unchanged: `-1` would conflate 'not declared' with
        a declared zero, and here the declared zero is the common case."""
        doc, ied = self.build((fx.sup_subscription("16", None),))
        limit = max_supervision(doc, ied)
        self.assertEqual(limit.max_go, 16)
        self.assertIsNone(limit.max_sv)

    def test_no_sup_subscription_is_unconstrained(self):
        """7 of 58 corpus IEDs declare none. Q26 §3's precedent: an
        undeclared limit is not a limit."""
        doc, ied = self.build((fx.conf_data_set(),))
        self.assertIsNone(max_supervision(doc, ied))

    def test_no_services_at_all_is_unconstrained(self):
        doc = self.station(publisher(), subscriber(nodes=(fx.supervision(),)))
        self.assertIsNone(max_supervision(doc, ied_of(doc, "SUB")))

    def test_it_is_found_from_anywhere_inside_the_ied(self):
        doc, ied = self.build((fx.sup_subscription("64", "60"),))
        node = _supervision_lns(doc, ied, "LGOS")[0]
        self.assertEqual(max_supervision(doc, node).max_go, 64)

    def test_an_access_point_declaration_is_read_first(self):
        """The reference reads the `AccessPoint`'s `Services` before the
        IED's. **No corpus file exercises it** -- 0 of 58 IEDs put a
        `SupSubscription` on an AccessPoint -- so this is the half of a
        documented rule that only a fixture can pin, exactly as Q26 found for
        `ConfDataSet`.

        Asked of the supervision node, which is what the guards do: an `IED`
        passed directly is not inside any of its own access points, so it can
        only ever find its own declaration."""
        ap = fx.access_point(
            "S1", fx.ldevice("CFG", fx.ln0() + fx.supervision()),
            services=fx.services(fx.sup_subscription("128", "60")))
        ied = fx.ied("SUB", ap + fx.services(fx.sup_subscription("16", "0")))
        doc = self.station(publisher(), ied)
        node = _supervision_lns(doc, ied_of(doc, "SUB"), "LGOS")[0]
        limit = max_supervision(doc, node)
        self.assertEqual((limit.max_go, limit.scope), (128, "AccessPoint"))
        self.assertEqual(max_supervision(doc, ied_of(doc, "SUB")).max_go, 16)

    def test_an_access_point_limit_governs_the_instantiation(self):
        """Q26 §2's rule, which the corpus cannot referee here either: the
        count is taken in the scope that declared the limit."""
        ap = fx.access_point(
            "S1", fx.ldevice("CFG", fx.ln0()
                             + fx.supervision(inst="1", cb_ref="X/LLN0.A")
                             + fx.supervision(inst="2")),
            services=fx.services(fx.sup_subscription("1", "0")))
        doc = self.station(publisher(), fx.ied("SUB", ap))
        sup = Supervision(ied_of(doc, "SUB"),
                          find(doc, "GSEControl", name="GC1"))
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(doc, sup)
        self.assertIn("AccessPoint SupSubscription", str(caught.exception))


# -- instantiating: reuse ---------------------------------------------------

class TestInstantiateReuse(SupervisionCase):

    def test_a_free_node_has_its_value_set_rather_than_rebuilt(self):
        """Q30's rule for a `P` element, one section down. The `Val` is
        already there, so its TEXT is set: a `Remove` plus an `Insert` cannot
        promise to put an element back where it was, and a rebuilt element
        loses whatever attributes the file gave it."""
        edits = instantiate_supervision(self.doc, self.sup)
        self.assertEqual(len(edits), 1)
        self.assertIsInstance(edits[0], SetTextContent)
        self.assertEqual(edits[0].text, PUB_CB_REF)

    def test_applying_it_makes_the_node_watch_the_block(self):
        self.doc.apply_edit(instantiate_supervision(self.doc, self.sup))
        self.assertEqual(_supervised_reference(self.doc, self.node()),
                         PUB_CB_REF)

    def test_it_is_the_object_reference_a9_builds(self):
        """Not a second spelling of the same string: the value written is
        exactly :func:`~py61850.scl.control_block_obj_ref`'s."""
        self.doc.apply_edit(instantiate_supervision(self.doc, self.sup))
        self.assertEqual(_supervised_reference(self.doc, self.node()),
                         control_block_obj_ref(self.doc, self.block))

    def test_the_lowest_numbered_free_node_is_taken(self):
        doc = self.station(publisher(), subscriber(nodes=(
            fx.supervision(inst="7"), fx.supervision(inst="2"),
            fx.supervision(inst="4"))))
        sub = ied_of(doc, "SUB")
        sup = Supervision(sub, find(doc, "GSEControl", name="GC1"))
        doc.apply_edit(instantiate_supervision(doc, sup))
        watching = [node.get("inst") for node in _supervision_lns(doc, sub)
                    if _supervised_reference(doc, node)]
        self.assertEqual(watching, ["2"])

    def test_a_bound_node_is_not_reused(self):
        doc = self.station(publisher(), subscriber(nodes=(
            fx.supervision(inst="1", cb_ref="OTHERLD/LLN0.GC9"),
            fx.supervision(inst="2"))))
        sub = ied_of(doc, "SUB")
        sup = Supervision(sub, find(doc, "GSEControl", name="GC1"))
        doc.apply_edit(instantiate_supervision(doc, sup))
        self.assertEqual(_supervised_reference(doc, _first_free(doc, sub, "LGOS")
                                               or sub), "")

    def test_a_sampled_value_block_takes_an_lsvs(self):
        doc = self.station(publisher(), subscriber(nodes=(
            fx.supervision(ln_class="LGOS"),
            fx.supervision(ln_class="LSVS"))))
        sub = ied_of(doc, "SUB")
        sup = Supervision(sub, find(doc, "SampledValueControl", name="SV1"))
        doc.apply_edit(instantiate_supervision(doc, sup))
        self.assertEqual(
            _supervised_reference(doc, _supervision_lns(doc, sub, "LSVS")[0]),
            PUB_SV_REF)
        self.assertEqual(
            _supervised_reference(doc, _supervision_lns(doc, sub, "LGOS")[0]), "")


class TestInstantiateFillsWhatIsMissing(DocumentCase):
    """The four depths the setting path can be missing at, and the corpus has
    two of them."""

    def build(self, shape):
        doc = self.station(publisher(),
                      subscriber(nodes=(fx.supervision(shape=shape),)))
        sub = ied_of(doc, "SUB")
        sup = Supervision(sub, find(doc, "GSEControl", name="GC1"))
        return doc, sub, sup

    def apply(self, shape):
        doc, sub, sup = self.build(shape)
        edits = instantiate_supervision(doc, sup)
        self.assertEqual(len(edits), 1)
        doc.apply_edit(edits)
        node = _supervision_lns(doc, sub, "LGOS")[0]
        self.assertEqual(_supervised_reference(doc, node), PUB_CB_REF)
        return edits[0]

    def test_an_existing_val_is_set(self):
        self.assertIsInstance(self.apply("val"), SetTextContent)

    def test_a_dai_without_a_val_gets_one(self):
        edit = self.apply("no_val")
        self.assertIsInstance(edit, Insert)
        self.assertEqual(edit.node.tag.rsplit("}", 1)[-1], "Val")

    def test_an_empty_doi_gets_a_dai_and_that_is_siemens_idle_shape(self):
        """**25 `LGOS` in `siemens.scd` are written exactly this way** --
        `<DOI name="GoCBRef"/>` with no `DAI` at all -- and they are free
        slots, not missing locations. Counting spare nodes per IED settles
        it: those relays hold 27 supervision nodes more than they have
        subscribed control blocks, against 25 absent plus 2 empty."""
        edit = self.apply("empty")
        self.assertIsInstance(edit, Insert)
        self.assertEqual(edit.node.get("name"), "setSrcRef")

    def test_a_node_with_no_doi_gets_the_whole_path(self):
        edit = self.apply("none")
        self.assertIsInstance(edit, Insert)
        self.assertEqual(edit.node.get("name"), "GoCBRef")

    def test_every_shape_counts_as_free(self):
        for shape in ("val", "no_val", "empty", "none"):
            doc, sub, sup = self.build(shape)
            self.assertTrue(can_instantiate_supervision(doc, sup), shape)


# -- instantiating: a new logical node --------------------------------------

class TestInstantiateNewNode(SupervisionCase):

    def new(self, doc, sup, **kwargs):
        edits = instantiate_supervision(doc, sup, new_supervision_ln=True,
                                        **kwargs)
        self.assertEqual(len(edits), 1)
        self.assertIsInstance(edits[0], Insert)
        return edits[0]

    def test_it_is_placed_in_the_ldevice_its_siblings_use(self):
        """No corpus IED holds one supervision class in more than one
        `LDevice`, so a sibling answers the question unambiguously
        everywhere."""
        edit = self.new(self.doc, self.sup)
        self.assertEqual(edit.parent.get("inst"), "CFG")

    def test_it_takes_the_ln_type_of_a_sibling(self):
        edit = self.new(self.doc, self.sup)
        self.assertEqual(edit.node.get("lnType"), "T_LGOS")

    def test_it_carries_the_reference_it_was_created_for(self):
        edit = self.new(self.doc, self.sup)
        self.doc.apply_edit([edit])
        self.assertEqual(
            _supervised_reference(self.doc, self.node(inst="2")), PUB_CB_REF)

    def test_it_carries_no_prefix_and_no_desc(self):
        """Neither is guessed at, and the corpus is why. SEL writes a prefix
        on 228 of `sel.scd`'s 231 supervision nodes -- naming the device being
        SUPERVISED -- Siemens writes none on any of its 101, and `mixed.scd`
        none on 266 of 268. A convention two vendors disagree about that
        completely belongs to a vendor library."""
        edit = self.new(self.doc, self.sup)
        self.assertIsNone(edit.node.get("prefix"))
        self.assertIsNone(edit.node.get("desc"))

    def test_the_inst_is_the_lowest_unused_and_holes_are_filled(self):
        """Corpus `inst` values run 1 to 31 and are not dense: 26 of the 59
        (IED, class) blocks have a hole in them."""
        doc = self.station(publisher(), subscriber(nodes=(
            fx.supervision(inst="1", cb_ref="X/LLN0.A"),
            fx.supervision(inst="3", cb_ref="X/LLN0.B"))))
        sup = Supervision(ied_of(doc, "SUB"),
                          find(doc, "GSEControl", name="GC1"))
        self.assertEqual(self.new(doc, sup).node.get("inst"), "2")

    def test_the_scan_ignores_prefix_so_it_cannot_collide(self):
        """`tLN`'s identity is prefix + lnClass + inst, so a prefix-aware
        allocator would find more free numbers. Scanning by class alone is
        the conservative rule the reference's own generator uses, and it is
        what keeps a new node from colliding with SEL's prefixed ones."""
        doc = self.station(publisher(), subscriber(nodes=(
            fx.supervision(inst="1", prefix="Q1_PUB", cb_ref="X/LLN0.A"),)))
        sup = Supervision(ied_of(doc, "SUB"),
                          find(doc, "GSEControl", name="GC1"))
        self.assertEqual(self.new(doc, sup).node.get("inst"), "2")

    def test_a_fixed_inst_is_used_as_given(self):
        self.assertEqual(
            self.new(self.doc, self.sup, fixed_ln_inst=17).node.get("inst"),
            "17")

    def test_a_fixed_inst_already_in_use_is_refused(self):
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(self.doc, self.sup,
                                    new_supervision_ln=True, fixed_ln_inst=1)
        self.assertIn("already in LDevice", str(caught.exception))

    def test_an_exhausted_range_is_refused(self):
        """**A17b changed the message and the reason is a correction.** It
        used to end "which is the whole range 61850-6 allows for `tLN@inst`",
        and 61850-6 allows no such thing: `tLNInst` is ``[0-9]{1,12}`` in both
        editions. That was the fourth place the false provenance was written
        down and the only one a caller could ever see. The scan is now
        `next_ln_inst`'s, so the wording is too."""
        low, high = LN_INST_RANGE
        nodes = tuple(fx.supervision(inst=str(i), cb_ref=f"X/LLN0.C{i}")
                      for i in range(low, high + 1))
        doc = self.station(publisher(), subscriber(nodes=nodes))
        sup = Supervision(ied_of(doc, "SUB"),
                          find(doc, "GSEControl", name="GC1"))
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(doc, sup, new_supervision_ln=True)
        message = str(caught.exception)
        self.assertIn("in use", message)
        self.assertIn(f"{low} to {high}", message)
        self.assertNotIn("61850-6", message)

    def test_an_ied_with_no_sibling_is_refused_and_says_what_is_missing(self):
        """**Seven corpus IEDs subscribe to GOOSE and hold no supervision
        node at all.** There is no `LDevice` to read and no `lnType` to copy,
        and inventing one would mean writing an `LNodeType` into
        `DataTypeTemplates` -- the dependency that kept `insert_ied` out of
        A13."""
        doc = self.station(publisher(), subscriber(nodes=()))
        sup = Supervision(ied_of(doc, "SUB"),
                          find(doc, "GSEControl", name="GC1"))
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(doc, sup, new_supervision_ln=True)
        message = str(caught.exception)
        self.assertIn("holds no LGOS", message)
        self.assertIn("pass parent and ln_type", message)

    def test_a_caller_who_knows_may_pass_parent_and_ln_type(self):
        doc = self.station(publisher(), subscriber(nodes=()))
        sub = ied_of(doc, "SUB")
        sup = Supervision(sub, find(doc, "GSEControl", name="GC1"))
        edits = instantiate_supervision(doc, sup, new_supervision_ln=True,
                                        parent=find(doc, "LDevice", inst="CFG"),
                                        ln_type="T_LGOS")
        doc.apply_edit(edits)
        self.assertEqual(
            _supervised_reference(doc, _supervision_lns(doc, sub, "LGOS")[0]),
            PUB_CB_REF)

    def test_a_parent_outside_the_subscriber_is_refused(self):
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(
                self.doc, self.sup, new_supervision_ln=True,
                parent=find(self.doc, "LDevice", inst="LD"))
        self.assertIn("not inside IED", str(caught.exception))

    def test_a_parent_that_is_not_an_ldevice_is_refused(self):
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(self.doc, self.sup,
                                    new_supervision_ln=True, parent=self.sub)
        self.assertIn("goes in an LDevice", str(caught.exception))


# -- instantiating: a named node --------------------------------------------

class TestInstantiateNamedNode(SupervisionCase):
    """``supervision_ln`` is the other half of the reference's union-typed
    `subscriberIedOrLn`, moved to where the other placement options are."""

    def test_the_named_node_is_the_one_written(self):
        doc = self.station(publisher(), subscriber(nodes=(
            fx.supervision(inst="1"), fx.supervision(inst="2"))))
        sub = ied_of(doc, "SUB")
        chosen = _supervision_lns(doc, sub, "LGOS")[1]
        sup = Supervision(sub, find(doc, "GSEControl", name="GC1"))
        doc.apply_edit(instantiate_supervision(doc, sup,
                                               supervision_ln=chosen))
        self.assertEqual(_supervised_reference(doc, chosen), PUB_CB_REF)

    def test_a_node_already_watching_something_is_refused(self):
        doc = self.station(publisher(), subscriber(nodes=(
            fx.supervision(inst="1", cb_ref="OTHER/LLN0.GC9"),)))
        sub = ied_of(doc, "SUB")
        taken = _supervision_lns(doc, sub, "LGOS")[0]
        sup = Supervision(sub, find(doc, "GSEControl", name="GC1"))
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(doc, sup, supervision_ln=taken)
        self.assertIn("already supervises OTHER/LLN0.GC9",
                      str(caught.exception))

    def test_a_node_of_the_wrong_class_is_refused(self):
        doc = self.station(publisher(), subscriber(nodes=(
            fx.supervision(ln_class="LSVS"),)))
        sub = ied_of(doc, "SUB")
        wrong = _supervision_lns(doc, sub, "LSVS")[0]
        sup = Supervision(sub, find(doc, "GSEControl", name="GC1"))
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(doc, sup, supervision_ln=wrong)
        self.assertIn("cannot supervise", str(caught.exception))

    def test_a_node_in_another_ied_is_refused(self):
        doc = self.station(publisher(),
                      subscriber("SUB", nodes=(fx.supervision(),)),
                      subscriber("SUB2", nodes=(fx.supervision(),)))
        other = _supervision_lns(doc, ied_of(doc, "SUB2"), "LGOS")[0]
        sup = Supervision(ied_of(doc, "SUB"),
                          find(doc, "GSEControl", name="GC1"))
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(doc, sup, supervision_ln=other)
        self.assertIn("not inside IED", str(caught.exception))

    def test_something_that_is_not_a_supervision_node_is_refused(self):
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(self.doc, self.sup,
                                    supervision_ln=self.block)
        self.assertIn("must be an LGOS or LSVS", str(caught.exception))


# -- the refusals -----------------------------------------------------------

class TestInstantiateRefusals(SupervisionCase):

    def test_a_duplicate_supervision_is_refused_and_names_the_node(self):
        self.doc.apply_edit(instantiate_supervision(self.doc, self.sup))
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(self.doc, self.sup,
                                    new_supervision_ln=True)
        self.assertIn("already supervises PUBLD/LLN0.GC1",
                      str(caught.exception))

    def test_the_duplicate_check_can_be_turned_off(self):
        self.doc.apply_edit(instantiate_supervision(self.doc, self.sup))
        edits = instantiate_supervision(self.doc, self.sup,
                                        new_supervision_ln=True,
                                        check_duplicate_supervisions=False)
        self.assertEqual(len(edits), 1)

    def test_no_free_node_is_refused_rather_than_silently_creating_one(self):
        doc = self.station(publisher(), subscriber(nodes=(
            fx.supervision(inst="1", cb_ref="OTHER/LLN0.GC9"),)))
        sup = Supervision(ied_of(doc, "SUB"),
                          find(doc, "GSEControl", name="GC1"))
        self.assertFalse(can_instantiate_supervision(doc, sup))
        self.assertTrue(can_instantiate_supervision(doc, sup,
                                                    new_supervision_ln=True))

    def test_an_explicitly_non_editable_value_is_refused(self):
        doc = self.station(publisher(), subscriber(nodes=(fx.supervision(),)),
                      val_kind="RO", val_import="false")
        sup = Supervision(ied_of(doc, "SUB"),
                          find(doc, "GSEControl", name="GC1"))
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(doc, sup)
        self.assertIn("not writable by a configuration tool",
                      str(caught.exception))

    def test_the_editable_check_can_be_turned_off(self):
        doc = self.station(publisher(), subscriber(nodes=(fx.supervision(),)),
                      val_kind="RO", val_import="false")
        sup = Supervision(ied_of(doc, "SUB"),
                          find(doc, "GSEControl", name="GC1"))
        self.assertEqual(
            len(instantiate_supervision(doc, sup,
                                        check_editable_src_ref=False)), 1)

    def test_a_new_node_checks_the_type_it_would_inherit(self):
        doc = self.station(publisher(), subscriber(nodes=(
            fx.supervision(inst="1", cb_ref="OTHER/LLN0.GC9"),)),
            val_kind="Set", val_import="false")
        sup = Supervision(ied_of(doc, "SUB"),
                          find(doc, "GSEControl", name="GC1"))
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(doc, sup, new_supervision_ln=True)
        self.assertIn("LNodeType", str(caught.exception))

    def test_a_report_control_is_refused_with_the_reason(self):
        doc = self.station(fx.ied("R", fx.access_point("S1", fx.ldevice(
            "LD", fx.ln0(body=fx.report_control("RC1"))))),
            subscriber(nodes=(fx.supervision(),)))
        sup = Supervision(ied_of(doc, "SUB"),
                          find(doc, "ReportControl", name="RC1"))
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(doc, sup)
        self.assertIn("no class for a ReportControl", str(caught.exception))

    def test_a_subscriber_that_is_not_an_ied_is_refused(self):
        sup = Supervision(subscriber=self.block, control_block=self.block)
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(self.doc, sup)
        self.assertIn("is not an IED of this document", str(caught.exception))

    def test_something_that_is_not_a_control_block_is_refused(self):
        sup = Supervision(subscriber=self.sub, control_block=self.sub)
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(self.doc, sup)
        self.assertIn("is not a control block", str(caught.exception))

    def test_a_supervision_must_be_a_supervision(self):
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(self.doc, (self.sub, self.block))
        self.assertIn("takes a Supervision", str(caught.exception))

    def test_a_rejected_edit_changes_nothing(self):
        """`RULES`' standing rule, and the reason nothing here applies."""
        before = self.doc.to_bytes()
        with self.assertRaises(EditRejected):
            instantiate_supervision(self.doc,
                                    Supervision(self.sub, self.sub))
        self.assertEqual(self.doc.to_bytes(), before)


# -- the Services guard in action -------------------------------------------

class TestSupervisionLimits(DocumentCase):

    def build(self, nodes, entries):
        doc = self.station(publisher(), with_services("SUB", nodes, entries))
        return doc, Supervision(ied_of(doc, "SUB"),
                                find(doc, "GSEControl", name="GC1"))

    def test_a_free_node_under_the_limit_is_allowed(self):
        doc, sup = self.build((fx.supervision(inst="1"),),
                              (fx.sup_subscription("16", "0"),))
        self.assertTrue(can_instantiate_supervision(doc, sup))

    def test_reaching_the_limit_refuses_and_names_the_declaration(self):
        nodes = tuple(fx.supervision(inst=str(i), cb_ref=f"X/LLN0.C{i}")
                      for i in range(1, 3)) + (fx.supervision(inst="3"),)
        doc, sup = self.build(nodes, (fx.sup_subscription("2", "0"),))
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(doc, sup)
        message = str(caught.exception)
        self.assertIn("maxGo=2", message)
        self.assertIn("IED SupSubscription", message)

    def test_the_count_is_of_BOUND_references_not_of_logical_nodes(self):
        """**The measurement that decides it.** `QPC4_TR1_UPC1` in `sel.scd`
        carries 26 `LGOS` against its own `maxGo="16"`, of which only 10 are
        pointed at anything. Counting elements would have this library refuse
        an edit because a vendor over-allocated its own supervision nodes;
        counting references agrees with the file. Q26 §1 reached the same
        answer for `maxAttributes` on weaker evidence."""
        nodes = tuple(fx.supervision(inst=str(i)) for i in range(1, 6))
        doc, sup = self.build(nodes, (fx.sup_subscription("2", "0"),))
        self.assertTrue(can_instantiate_supervision(doc, sup))

    def test_max_sv_zero_refuses_every_sampled_value_supervision(self):
        """43 of 51 corpus IEDs declare it, so this refusal has more vendor
        data behind it than the GOOSE one, which has a single witness."""
        doc = self.station(publisher(), with_services(
            "SUB", (fx.supervision(ln_class="LSVS"),),
            (fx.sup_subscription("16", "0"),)))
        sup = Supervision(ied_of(doc, "SUB"),
                          find(doc, "SampledValueControl", name="SV1"))
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(doc, sup)
        self.assertIn("maxSv=0", str(caught.exception))

    def test_an_undeclared_limit_is_not_a_limit(self):
        nodes = tuple(fx.supervision(inst=str(i), cb_ref=f"X/LLN0.C{i}")
                      for i in range(1, 40)) + (fx.supervision(inst="40"),)
        doc, sup = self.build(nodes, (fx.conf_data_set(),))
        self.assertTrue(can_instantiate_supervision(doc, sup))

    def test_the_limit_check_can_be_turned_off(self):
        nodes = (fx.supervision(inst="1", cb_ref="X/LLN0.C1"),
                 fx.supervision(inst="2"))
        doc, sup = self.build(nodes, (fx.sup_subscription("1", "0"),))
        self.assertFalse(can_instantiate_supervision(doc, sup))
        self.assertTrue(can_instantiate_supervision(
            doc, sup, check_max_supervision_limits=False))


# -- the guard and the builder agree ----------------------------------------

class TestGuardAgreesWithBuilder(SupervisionCase):
    """`can_instantiate_supervision` is the planner returning without
    raising, so the two cannot reach different conclusions -- which is the
    drift Q23 and Q25 exist to stop."""

    CASES = (
        {},
        {"new_supervision_ln": True},
        {"new_supervision_ln": True, "fixed_ln_inst": 1},
        {"check_editable_src_ref": False},
        {"supervision_ln": None},
    )

    def test_every_option_set_agrees(self):
        for kwargs in self.CASES:
            doc = self.station(publisher(), subscriber(nodes=(fx.supervision(),)))
            sup = Supervision(ied_of(doc, "SUB"),
                              find(doc, "GSEControl", name="GC1"))
            allowed = can_instantiate_supervision(doc, sup, **kwargs)
            try:
                instantiate_supervision(doc, sup, **kwargs)
            except EditRejected:
                self.assertFalse(allowed, kwargs)
            else:
                self.assertTrue(allowed, kwargs)


# -- removing ---------------------------------------------------------------

class TestRemoveSupervision(SupervisionCase):

    def bound(self, **kwargs):
        doc = self.station(publisher(), subscriber(nodes=(
            fx.supervision(inst="1", cb_ref=PUB_CB_REF),)), **kwargs)
        return doc, _supervision_lns(doc, ied_of(doc, "SUB"), "LGOS")[0]

    def test_the_value_is_blanked_and_the_node_is_kept(self):
        """**Two unrelated vendors keep an idle supervision node standing**
        -- 24 `LGOS` in `sel.scd` with an empty `Val`, 25 in `siemens.scd`
        with an empty `DOI` -- so a blanked node is an ordinary shape in a
        station file. Removing the element instead would renumber the `inst`
        of the ones after it."""
        doc, node = self.bound()
        edits = remove_supervision(doc, node)
        self.assertEqual(len(edits), 1)
        self.assertIsInstance(edits[0], SetTextContent)
        doc.apply_edit(edits)
        self.assertEqual(_supervised_reference(doc, node), "")
        self.assertIn(node, list(doc.parent_of(node)))

    def test_it_is_the_same_edit_a13_produces_for_a_removed_ied(self):
        """A13's `remove_ied` blanks the same value, with the same primitive.
        Two paths leaving a stale supervision in two different shapes is the
        drift Q25 was written about, so the shape is pinned on both sides:
        the `Val` element stays, carrying no text."""
        doc, node = self.bound()
        doc.apply_edit(remove_supervision(doc, node))
        doi = next(c for c in node if c.get("name") == "GoCBRef")
        val = next(iter_local(doi, "Val"))
        self.assertIsNotNone(val)
        self.assertFalse((val.text or "").strip())

    def test_remove_supervision_ln_takes_the_whole_node(self):
        doc, node = self.bound()
        edits = remove_supervision(doc, node, remove_supervision_ln=True)
        self.assertEqual(len(edits), 1)
        self.assertIsInstance(edits[0], Remove)
        doc.apply_edit(edits)
        self.assertEqual(_supervision_lns(doc, ied_of(doc, "SUB"), "LGOS"), [])

    def test_a_node_that_watches_nothing_returns_no_edits(self):
        """Not a refusal: there is nothing to do and saying so is honest."""
        self.assertEqual(remove_supervision(self.doc, self.node()), [])
        self.assertTrue(can_remove_supervision(self.doc, self.node()))

    def test_a_still_subscribed_block_is_refused(self):
        """The reference removes supervision *"when all external references
        of one control block are unsubscribed"*. Here that is a guard rather
        than a side effect, so a caller cannot half-unsubscribe and lose the
        supervision anyway."""
        ext = fx.inputs(fx.ext_ref(iedName="PUB", ldInst="LD", lnClass="MMXU",
                                   doName="TotW", daName="mag.f",
                                   serviceType="GOOSE", srcLDInst="LD",
                                   srcCBName="GC1", intAddr="A"))
        doc = self.station(publisher(), subscriber(nodes=(
            fx.supervision(inst="1", cb_ref=PUB_CB_REF),
            fx.ln("MMXU", inst="1", body=ext))))
        node = _supervision_lns(doc, ied_of(doc, "SUB"), "LGOS")[0]
        self.assertFalse(can_remove_supervision(doc, node))
        with self.assertRaises(EditRejected) as caught:
            remove_supervision(doc, node)
        self.assertIn("still has 1 ExtRef", str(caught.exception))
        self.assertEqual(len(remove_supervision(doc, node,
                                                check_subscription=False)), 1)

    def test_a_dangling_reference_is_blanked_without_complaint(self):
        """A value naming a control block this document does not hold cannot
        have subscribers in it either, so there is nothing to check."""
        doc = self.station(publisher(), subscriber(nodes=(
            fx.supervision(inst="1", cb_ref="GONE/LLN0.GC9"),)))
        node = _supervision_lns(doc, ied_of(doc, "SUB"), "LGOS")[0]
        self.assertEqual(len(remove_supervision(doc, node)), 1)

    def test_something_that_is_not_a_supervision_node_is_refused(self):
        with self.assertRaises(EditRejected) as caught:
            remove_supervision(self.doc, self.block)
        self.assertIn("takes an LGOS or LSVS", str(caught.exception))
        self.assertFalse(can_remove_supervision(self.doc, self.block))


# -- invertibility ----------------------------------------------------------

class TestInvertibility(SupervisionCase):
    """Stage 0's guarantee composed with A5's: an edited document that is
    fully undone is the file it came from, byte for byte."""

    def cycle(self, build):
        doc = self.station(publisher(), subscriber(nodes=(
            fx.supervision(inst="1"), fx.supervision(inst="2",
                                                     cb_ref=PUB_SV_REF))))
        before = doc.to_bytes()
        undo = doc.apply_edit(build(doc))
        self.assertNotEqual(doc.to_bytes(), before)
        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), before)

    def test_reuse_inverts_byte_for_byte(self):
        self.cycle(lambda doc: instantiate_supervision(
            doc, Supervision(ied_of(doc, "SUB"),
                             find(doc, "GSEControl", name="GC1"))))

    def test_a_new_node_inverts_byte_for_byte(self):
        self.cycle(lambda doc: instantiate_supervision(
            doc, Supervision(ied_of(doc, "SUB"),
                             find(doc, "GSEControl", name="GC1")),
            new_supervision_ln=True))

    def test_blanking_inverts_byte_for_byte(self):
        self.cycle(lambda doc: remove_supervision(
            doc, _supervision_lns(doc, ied_of(doc, "SUB"), "LGOS")[1]))

    def test_removing_the_node_inverts_byte_for_byte(self):
        self.cycle(lambda doc: remove_supervision(
            doc, _supervision_lns(doc, ied_of(doc, "SUB"), "LGOS")[1],
            remove_supervision_ln=True))


# -- the wiring: what the seven flags actually do ---------------------------
#
# A14b's own tests. The seven `ignore_supervision` flags stop refusing and
# start working, and what each `False` means is asserted here because this is
# where the fixtures that can express it live -- a publisher, a subscriber
# with supervision nodes, and `ExtRef` elements between them. The five modules
# that CALL this one assert what their own station can say and no more.

class WiringCase(DocumentCase):
    """A publisher, and a subscriber that both subscribes to it and could
    supervise it."""

    def build(self, nodes=(), inputs=(), blocks=None, reports=False,
              services=None, members=1):
        doc = self.station(publisher(blocks=blocks, reports=reports,
                                     members=members),
                           subscriber(nodes=nodes, inputs=inputs)
                           if services is None
                           else with_services("SUB", nodes, services,
                                              inputs=inputs))
        self.doc = doc
        self.pub = ied_of(doc, "PUB")
        self.sub = ied_of(doc, "SUB")
        return doc

    def block(self, name="GC1"):
        return find(self.doc, "GSEControl", name=name)

    def fcda(self):
        return next(iter_local(self.doc.root, "FCDA"))

    def ext_refs(self):
        return [element for element in iter_local(self.sub, "ExtRef")]

    def watched(self, ln_class="LGOS"):
        return [_supervised_reference(self.doc, node)
                for node in _supervision_lns(self.doc, self.sub, ln_class)]


class TestSubscribeWiring(WiringCase):
    """`subscribe(..., ignore_supervision=False)` instantiates what each
    connection implies."""

    def connect(self, sink=None, block="GC1"):
        return Connection(sink if sink is not None else self.ext_refs()[0],
                          self.fcda(), self.block(block))

    def test_a_free_slot_is_filled_with_the_blocks_object_reference(self):
        self.build(nodes=(fx.supervision(inst="1"),), inputs=(unbound(),))
        self.assertEqual(self.watched(), [""])
        self.doc.apply_edit(subscribe(self.doc, self.connect(),
                                      ignore_supervision=False))
        self.assertEqual(self.watched(), [PUB_CB_REF])

    def test_the_default_writes_none_of_it(self):
        """`True` is the default and stays it. Flipping to the reference's
        `false` would change what every existing caller writes into a file,
        which A18's MINOR rule forbids -- Q32 §9."""
        self.build(nodes=(fx.supervision(inst="1"),), inputs=(unbound(),))
        self.doc.apply_edit(subscribe(self.doc, self.connect()))
        self.assertEqual(self.watched(), [""])

    def test_a_block_this_ied_already_supervises_is_left_alone(self):
        """**Skipped, not refused.** 548 of the corpus's 566 (subscriber,
        control block) pairs are already supervised, so the duplicate is the
        ordinary case here -- where for a caller asking for ONE supervision it
        is an error, which is what `instantiate_supervision` still says."""
        self.build(nodes=(fx.supervision(inst="1", cb_ref=PUB_CB_REF),
                          fx.supervision(inst="2")),
                   inputs=(unbound(),))
        self.doc.apply_edit(subscribe(self.doc, self.connect(),
                                      ignore_supervision=False))
        self.assertEqual(self.watched(), [PUB_CB_REF, ""])

    def test_two_extrefs_to_one_block_are_one_supervision(self):
        """One subscription to supervise, not two. 531 of the corpus's 566
        pairs carry more than one `ExtRef`, so this is the common shape."""
        self.build(nodes=(fx.supervision(inst="1"), fx.supervision(inst="2")),
                   inputs=(unbound("a"), unbound("b")))
        refs = self.ext_refs()
        self.doc.apply_edit(subscribe(
            self.doc, [self.connect(refs[0]), self.connect(refs[1])],
            ignore_supervision=False))
        self.assertEqual(self.watched(), [PUB_CB_REF, ""])

    def test_a_report_control_produces_no_supervision(self):
        """61850-7-4 defines `LGOS` for a `GSEControl` and `LSVS` for a
        `SampledValueControl` and no report equivalent, so there is nothing to
        write -- and nothing to complain about either."""
        self.build(nodes=(fx.supervision(inst="1"),), inputs=(unbound(),),
                   reports=True)
        connection = Connection(self.ext_refs()[0], self.fcda(),
                                find(self.doc, "ReportControl", name="RC1"))
        self.doc.apply_edit(subscribe(self.doc, connection,
                                      ignore_supervision=False))
        self.assertEqual(self.watched(), [""])

    def test_a_connection_naming_no_control_block_produces_none_either(self):
        """An Edition 1 subscription names no control block at all, so there
        is nothing to supervise and no way to find out what would be."""
        self.build(nodes=(fx.supervision(inst="1"),), inputs=(unbound(),))
        connection = Connection(self.ext_refs()[0], self.fcda(), None)
        self.doc.apply_edit(subscribe(self.doc, connection,
                                      ignore_supervision=False))
        self.assertEqual(self.watched(), [""])

    def test_no_free_slot_refuses_rather_than_building_one(self):
        """Q27's rule, which A14 already applied to
        `instantiate_supervision`: "fill in a free slot" and "add a logical
        node to this IED" are different sizes of action and the larger is
        asked for. 45 of the corpus's 59 (IED, class) blocks are fully bound,
        so this is the common answer and not an edge."""
        self.build(nodes=(fx.supervision(inst="1", cb_ref="PUBLD/LLN0.OTHER"),),
                   inputs=(unbound(),))
        before = self.doc.to_bytes()
        with self.assertRaises(EditRejected) as caught:
            subscribe(self.doc, self.connect(), ignore_supervision=False)
        self.assertIn("no free LGOS", str(caught.exception))
        self.assertEqual(self.doc.to_bytes(), before)

    def test_a_caller_who_means_it_may_build_one(self):
        self.build(nodes=(fx.supervision(inst="1", cb_ref="PUBLD/LLN0.OTHER"),),
                   inputs=(unbound(),))
        self.doc.apply_edit(subscribe(self.doc, self.connect(),
                                      ignore_supervision=False,
                                      new_supervision_ln=True))
        self.assertEqual(self.watched(), ["PUBLD/LLN0.OTHER", PUB_CB_REF])

    def test_an_ied_with_no_supervision_node_at_all_refuses_both_ways(self):
        """**All 16 unsupervised subscriptions in the corpus are this shape**,
        on the seven IEDs that hold no supervision node at all. A new node
        would need an `lnType` only `importLNodeType` can supply, so neither
        the default nor `new_supervision_ln=True` can serve them -- which is
        what makes the refusal decision in Q33 the one that matters."""
        self.build(inputs=(unbound(),))
        with self.assertRaises(EditRejected):
            subscribe(self.doc, self.connect(), ignore_supervision=False)
        with self.assertRaises(EditRejected) as caught:
            subscribe(self.doc, self.connect(), ignore_supervision=False,
                      new_supervision_ln=True)
        self.assertIn("lnType", str(caught.exception))

    def test_the_binding_and_the_supervision_are_one_history_entry(self):
        self.build(nodes=(fx.supervision(inst="1"),), inputs=(unbound(),))
        before = self.doc.to_bytes()
        undo = self.doc.apply_edit(subscribe(self.doc, self.connect(),
                                             ignore_supervision=False))
        self.assertNotEqual(self.doc.to_bytes(), before)
        self.doc.apply_edit(undo)
        self.assertEqual(self.doc.to_bytes(), before)


class TestSubscribeBatchClaims(WiringCase):
    """**Two supervisions planned in one call cannot take the same slot.**

    Every question this module asks is asked of the DOCUMENT, and inside one
    compound edit the document is out of date -- nothing is applied until the
    caller applies the whole list. So without `_Batch` the second connection
    would pick the same free `LGOS` as the first, the second `SetTextContent`
    would overwrite the first, and one supervision would be lost with no error
    anywhere. The reference keeps a `usedSupervisions` set for exactly this,
    and `subscribe` already keeps `created_inputs` for the identical hazard
    one level down.

    It is not an edge: 8 of the corpus's 59 (IED, class) blocks hold exactly
    one free slot, and one IED subscribes to 33 distinct control blocks.
    """

    def two(self):
        self.build(nodes=(fx.supervision(inst="1"), fx.supervision(inst="2")),
                   inputs=(unbound("a"), unbound("b")), blocks=("GC2",))
        refs = self.ext_refs()
        return [Connection(refs[0], self.fcda(), self.block("GC1")),
                Connection(refs[1], self.fcda(), self.block("GC2"))]

    def test_two_supervisions_in_one_call_take_two_slots(self):
        connections = self.two()
        self.doc.apply_edit(subscribe(self.doc, connections,
                                      ignore_supervision=False))
        self.assertEqual(self.watched(), [PUB_CB_REF, "PUBLD/LLN0.GC2"])

    def test_neither_reference_is_lost(self):
        """The failure this guards against is silent, so it is worth asserting
        from the other side too: both references are in the file, and the
        edits are on two different elements."""
        connections = self.two()
        edits = subscribe(self.doc, connections, ignore_supervision=False)
        targets = [id(e.element) for e in edits
                   if isinstance(e, SetTextContent)]
        self.assertEqual(len(targets), 2)
        self.assertEqual(len(set(targets)), 2)

    def test_two_new_logical_nodes_in_one_call_get_different_insts(self):
        """The same collision one level down: `_free_inst` scans the document,
        which does not yet hold the node the first connection is adding."""
        self.build(nodes=(fx.supervision(inst="1", cb_ref="PUBLD/LLN0.OTHER"),),
                   inputs=(unbound("a"), unbound("b")), blocks=("GC2",))
        refs = self.ext_refs()
        self.doc.apply_edit(subscribe(self.doc, [
            Connection(refs[0], self.fcda(), self.block("GC1")),
            Connection(refs[1], self.fcda(), self.block("GC2"))],
            ignore_supervision=False, new_supervision_ln=True))
        nodes = _supervision_lns(self.doc, self.sub, "LGOS")
        self.assertEqual([node.get("inst") for node in nodes],
                         ["1", "2", "3"])
        self.assertEqual(self.watched(),
                         ["PUBLD/LLN0.OTHER", PUB_CB_REF, "PUBLD/LLN0.GC2"])

    def test_a_batch_cannot_spend_the_last_declared_place_twice(self):
        """`Services/SupSubscription` counts BOUND references -- Q32 §4 -- and
        the count a batch has to work from is the document's plus what this
        same edit has already promised. With `maxGo="1"` and two free slots,
        the second connection is over the limit even though the file still
        shows none bound."""
        self.build(nodes=(fx.supervision(inst="1"), fx.supervision(inst="2")),
                   inputs=(unbound("a"), unbound("b")), blocks=("GC2",),
                   services=(fx.sup_subscription(max_go="1"),))
        refs = self.ext_refs()
        before = self.doc.to_bytes()
        with self.assertRaises(EditRejected) as caught:
            subscribe(self.doc, [
                Connection(refs[0], self.fcda(), self.block("GC1")),
                Connection(refs[1], self.fcda(), self.block("GC2"))],
                ignore_supervision=False)
        self.assertIn("maxGo=1", str(caught.exception))
        self.assertEqual(self.doc.to_bytes(), before)

    def test_one_of_them_alone_is_inside_the_limit(self):
        """The other half of the test above: the refusal is the batch's doing,
        not the limit refusing everything."""
        self.build(nodes=(fx.supervision(inst="1"), fx.supervision(inst="2")),
                   inputs=(unbound("a"),),
                   services=(fx.sup_subscription(max_go="1"),))
        self.doc.apply_edit(subscribe(
            self.doc, Connection(self.ext_refs()[0], self.fcda(),
                                 self.block("GC1")),
            ignore_supervision=False))
        self.assertEqual(self.watched(), [PUB_CB_REF, ""])


class TestUnsubscribeWiring(WiringCase):
    """`unsubscribe(..., ignore_supervision=False)` blanks the supervision
    whose LAST `ExtRef` is going."""

    def bound(self, refs=("in1",), cb="GC1", nodes=None):
        if nodes is None:
            nodes = (fx.supervision(inst="1", cb_ref=PUB_CB_REF),)
        self.build(nodes=nodes,
                   inputs=tuple(subscribed(cb, addr) for addr in refs))
        return self.ext_refs()

    def test_the_last_extref_going_blanks_it(self):
        refs = self.bound()
        self.doc.apply_edit(unsubscribe(self.doc, refs,
                                        ignore_supervision=False))
        self.assertEqual(self.watched(), [""])

    def test_an_extref_that_is_not_the_last_leaves_it_standing(self):
        """**The rule is about a SET.** Only 35 of the corpus's 566 pairs
        carry a single `ExtRef` and the busiest block carries 64, so judging
        one call at a time would make the answer depend on the order the
        caller happened to ask in. Q27's argument for `remove_fcda`'s list,
        arriving at `unsubscribe`."""
        refs = self.bound(refs=("in1", "in2"))
        self.doc.apply_edit(unsubscribe(self.doc, refs[0],
                                        ignore_supervision=False))
        self.assertEqual(self.watched(), [PUB_CB_REF])

    def test_all_of_them_going_together_blanks_it_once(self):
        refs = self.bound(refs=("in1", "in2", "in3"))
        edits = unsubscribe(self.doc, refs, ignore_supervision=False)
        blanks = [e for e in edits if isinstance(e, SetTextContent)]
        self.assertEqual(len(blanks), 1)
        self.doc.apply_edit(edits)
        self.assertEqual(self.watched(), [""])

    def test_two_spellings_of_one_binding_are_still_one_block(self):
        """**An absent attribute and an empty one are the same value here**,
        which `_same` says and `find_control_block_subscription` relies on --
        SCL omits `srcPrefix` rather than writing it empty, and a comparison
        that told them apart would call an `ExtRef` unbound from the block it
        is bound to.

        So two ExtRefs on one control block can be spelled differently and
        group differently. Counting each group's own departures would then let
        each see the OTHER's as ExtRefs that are staying, and a supervision
        whose every subscription was going would be left watching nothing that
        exists. The departures are counted once for the whole call instead.
        """
        self.build(nodes=(fx.supervision(inst="1", cb_ref=PUB_CB_REF),),
                   inputs=(subscribed("GC1", "in1"),
                           fx.ext_ref(iedName="PUB", ldInst="LD",
                                      lnClass="MMXU", doName="TotW",
                                      daName="mag.f", intAddr="in2",
                                      serviceType="GOOSE", srcCBName="GC1",
                                      srcLDInst="LD", srcLNClass="LLN0",
                                      srcPrefix="")))
        refs = self.ext_refs()
        self.assertNotEqual(refs[0].get("srcPrefix"), refs[1].get("srcPrefix"))
        self.doc.apply_edit(unsubscribe(self.doc, refs,
                                        ignore_supervision=False))
        self.assertEqual(self.watched(), [""])

    def test_the_default_leaves_the_supervision_standing(self):
        refs = self.bound()
        self.doc.apply_edit(unsubscribe(self.doc, refs))
        self.assertEqual(self.watched(), [PUB_CB_REF])

    def test_the_node_is_kept_and_only_the_value_goes(self):
        """`remove_supervision`'s default, which is also the edit A13's
        `remove_ied` emits -- the same primitive on the same element, so the
        three paths cannot drift. Two unrelated vendors write an idle
        supervision node rather than deleting one, 49 times between them."""
        refs = self.bound()
        edits = unsubscribe(self.doc, refs, ignore_supervision=False)
        self.assertEqual([e for e in edits if isinstance(e, Remove)], [])
        blanks = [e for e in edits if isinstance(e, SetTextContent)]
        self.assertEqual([e.text for e in blanks], [None])
        self.doc.apply_edit(edits)
        self.assertEqual(len(_supervision_lns(self.doc, self.sub, "LGOS")), 1)

    def test_a_supervision_of_another_block_is_not_touched(self):
        refs = self.bound(nodes=(
            fx.supervision(inst="1", cb_ref=PUB_CB_REF),
            fx.supervision(inst="2", cb_ref="PUBLD/LLN0.OTHER")))
        self.doc.apply_edit(unsubscribe(self.doc, refs,
                                        ignore_supervision=False))
        self.assertEqual(self.watched(), ["", "PUBLD/LLN0.OTHER"])

    def test_it_is_one_history_entry(self):
        refs = self.bound()
        before = self.doc.to_bytes()
        undo = self.doc.apply_edit(unsubscribe(self.doc, refs,
                                               ignore_supervision=False))
        self.assertNotEqual(self.doc.to_bytes(), before)
        self.doc.apply_edit(undo)
        self.assertEqual(self.doc.to_bytes(), before)


class TestRemovalWiringReachesSupervision(WiringCase):
    """The three removal functions own no expansion of their own except
    `remove_control_block`'s block-driven sweep; the rest reaches supervision
    through the `unsubscribe` they already perform. These assert the chain
    actually arrives, which the callers' own test modules cannot: their
    stations hold no supervision node."""

    def bound(self, refs=("in1",), members=1):
        self.build(nodes=(fx.supervision(inst="1", cb_ref=PUB_CB_REF),),
                   inputs=tuple(subscribed("GC1", addr) for addr in refs),
                   members=members)

    def test_removing_the_dataset_blanks_it_through_unsubscribe(self):
        self.bound()
        data_set = find(self.doc, "DataSet", name="DS1")
        self.doc.apply_edit(remove_data_set(self.doc, Remove(data_set),
                                            ignore_supervision=False))
        self.assertEqual(self.watched(), [""])

    def test_removing_the_last_member_blanks_it_too(self):
        """`remove_fcda`'s supervision half is almost always a no-op -- a
        member only empties a pair when it unbinds that pair's last `ExtRef`,
        and 531 of 566 pairs have more than one. This is the 35-pair case
        where it is not nothing."""
        self.bound(members=2)
        member = next(iter_local(self.doc.root, "FCDA"))
        self.doc.apply_edit(remove_fcda(self.doc, Remove(member),
                                        ignore_supervision=False,
                                        update_conf_rev=False))
        self.assertEqual(self.watched(), [""])

    def test_the_default_reaches_none_of_it(self):
        self.bound()
        data_set = find(self.doc, "DataSet", name="DS1")
        self.doc.apply_edit(remove_data_set(self.doc, Remove(data_set)))
        self.assertEqual(self.watched(), [PUB_CB_REF])

    def test_removing_the_block_blanks_it_even_with_nothing_subscribed(self):
        """**The block-driven sweep, and the case that tells it apart from the
        ExtRef-driven one.** Here the supervision names a block nobody
        subscribes to -- which happens 0 times in the corpus, so the two
        readings agree on every real file and only a fixture can separate
        them. A13's `remove_ied` already sweeps this way; without it, removing
        an IED and removing one of its control blocks would leave different
        supervision behind. Q33."""
        self.build(nodes=(fx.supervision(inst="1", cb_ref=PUB_CB_REF),))
        self.assertEqual(self.watched(), [PUB_CB_REF])
        self.doc.apply_edit(remove_control_block(
            self.doc, Remove(self.block("GC1")), ignore_supervision=False))
        self.assertEqual(self.watched(), [""])


# -- the reference corpus ---------------------------------------------------

class TestCorpus(DocumentCase):
    """600 supervision logical nodes of real vendor markup, and every
    measurement this module's decisions were taken on."""

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

    def nodes(self, doc, ln_class=None):
        return [node for ied in self.ieds(doc)
                for node in _supervision_lns(doc, ied, ln_class)]

    def test_the_corpus_holds_six_hundred_supervision_nodes(self):
        """585 `LGOS` and 15 `LSVS`, the sampled-value ones all in
        `mixed.scd`."""
        counts = {name: (len(self.nodes(self.corpus(name), "LGOS")),
                         len(self.nodes(self.corpus(name), "LSVS")))
                  for name in self.FILES}
        self.assertEqual(counts, {"sel.scd": (231, 0), "mixed.scd": (253, 15),
                                  "siemens.scd": (101, 0)})

    def test_five_hundred_and_forty_nine_are_bound(self):
        """The same 549 A13 measured from the rename side, counted here from
        the supervision side; the other 51 are free slots."""
        bound = sum(1 for name in self.FILES
                    for doc in [self.corpus(name)]
                    for node in self.nodes(doc)
                    if _supervised_reference(doc, node))
        self.assertEqual(bound, 549)

    def test_the_free_slots_are_written_in_two_vendor_shapes(self):
        """**26 with an empty `Val` and 25 with an empty `DOI`**, and both
        are free. The second looks like a missing location and is not: those
        25 are in `siemens.scd`, whose relays hold 27 more supervision nodes
        than they have subscribed control blocks."""
        empty_val = absent = 0
        for name in self.FILES:
            doc = self.corpus(name)
            namespace = self.namespace(doc)
            for node in self.nodes(doc):
                wanted = ("SvCBRef" if node.get("lnClass") == "LSVS"
                          else "GoCBRef")
                doi = next((c for c in node if c.tag == namespace + "DOI"
                            and c.get("name") == wanted), None)
                dai = None if doi is None else next(
                    (d for d in doi.iter(namespace + "DAI")
                     if d.get("name") == "setSrcRef"), None)
                if dai is None:
                    absent += 1
                elif not _supervised_reference(doc, node):
                    empty_val += 1
        self.assertEqual((empty_val, absent), (26, 25))

    def test_every_free_slot_can_be_instantiated_into(self):
        """The behavioural form of the measurement above: if the Siemens
        empty-`DOI` shape were read as 'no available location', 25 of the 51
        free slots in the corpus would be unusable."""
        for name in self.FILES:
            doc = self.corpus(name)
            for ied in self.ieds(doc):
                free = _first_free(doc, ied, "LGOS")
                if free is None:
                    continue
                block = self.some_unwatched_block(doc, ied)
                if block is None:
                    continue
                sup = Supervision(ied, block)
                self.assertTrue(can_instantiate_supervision(doc, sup),
                                f"{name} {ied.get('name')}")

    def some_unwatched_block(self, doc, ied):
        namespace = self.namespace(doc)
        watched = {_supervised_reference(doc, node)
                   for node in _supervision_lns(doc, ied, "LGOS")}
        for other in self.ieds(doc):
            for block in other.iter(namespace + "GSEControl"):
                reference = control_block_obj_ref(doc, block)
                if reference and reference not in watched:
                    return block
        return None

    def test_every_corpus_value_is_editable_under_our_reading(self):
        """**600 of 600**, where the reference's rule with the schema
        defaults applied would allow 406. Not one of the 194 it rejects
        carries an explicit refusal: 157 declare neither attribute at either
        level and 37 declare `valKind="RO"` with `valImport` absent."""
        for name in self.FILES:
            doc = self.corpus(name)
            for node in self.nodes(doc):
                self.assertTrue(is_src_ref_editable(doc, node),
                                f"{name} {node.get('lnType')}")

    def test_no_supervision_dai_in_the_corpus_declares_val_kind(self):
        """Which is why reading the instance alone gets the wrong answer:
        all 575 supervision `setSrcRef` instances are silent about it and the
        `DOType` behind them declares it 443 times. Other `DAI` elements in
        `sel.scd` do write `valKind`, which is why this is scoped."""
        seen = 0
        for name in self.FILES:
            doc = self.corpus(name)
            namespace = self.namespace(doc)
            for node in self.nodes(doc):
                wanted = ("SvCBRef" if node.get("lnClass") == "LSVS"
                          else "GoCBRef")
                for doi in node:
                    if (doi.tag != namespace + "DOI"
                            or doi.get("name") != wanted):
                        continue
                    for dai in doi.iter(namespace + "DAI"):
                        if dai.get("name") != "setSrcRef":
                            continue
                        seen += 1
                        self.assertIsNone(dai.get("valKind"), name)
        self.assertEqual(seen, 575)

    def test_fifty_one_ieds_declare_a_sup_subscription(self):
        """`maxGo` is 16, 64, 128 or 150 and `maxSv` 0 or 60; **43 of the 51
        declare `maxSv="0"`.** The other 7 IEDs declare none at all and are
        unconstrained."""
        declared = [max_supervision(doc, ied)
                    for name in self.FILES for doc in [self.corpus(name)]
                    for ied in self.ieds(doc)]
        found = [limit for limit in declared if limit is not None]
        self.assertEqual((len(declared), len(found)), (58, 51))
        self.assertEqual(sorted({limit.max_go for limit in found}),
                         [16, 64, 128, 150])
        self.assertEqual(sum(1 for limit in found if limit.max_sv == 0), 43)

    def test_no_access_point_declares_one(self):
        """Q26 found the same of `ConfDataSet`: the AccessPoint-first half of
        the rule is implemented and no file exercises it."""
        self.assertEqual(
            [limit.scope for name in self.FILES
             for doc in [self.corpus(name)] for ied in self.ieds(doc)
             for limit in [max_supervision(doc, ied)] if limit is not None
             and limit.scope != "IED"], [])

    def test_one_ied_is_over_its_own_declared_limit_by_instances(self):
        """**`QPC4_TR1_UPC1` carries 26 `LGOS` against `maxGo="16"`**, and 10
        of them are bound. This is the whole argument for counting bound
        references: the instance reading makes a vendor violate its own
        export, and the bound reading does not."""
        doc = self.corpus("sel.scd")
        ied = next(i for i in self.ieds(doc)
                   if i.get("name") == "QPC4_TR1_UPC1")
        self.assertEqual(len(_supervision_lns(doc, ied, "LGOS")), 26)
        self.assertEqual(_bound_supervisions(doc, ied, "LGOS"), 10)
        self.assertEqual(max_supervision(doc, ied).max_go, 16)
        self.assertTrue(can_instantiate_supervision(
            doc, Supervision(ied, self.some_unwatched_block(doc, ied))))

    def test_no_ied_holds_one_class_in_more_than_one_ldevice(self):
        """Which is what makes 'the LDevice its siblings use' an unambiguous
        answer. `mixed.scd` does split the two CLASSES across two logical
        devices -- `ComSupervision_GOOSE` and `ComSupervision_SV` -- so the
        choice is per class, not per IED."""
        for name in self.FILES:
            doc = self.corpus(name)
            for ied in self.ieds(doc):
                for ln_class in ("LGOS", "LSVS"):
                    parents = {doc.parent_of(node).get("inst")
                               for node in _supervision_lns(doc, ied, ln_class)}
                    self.assertLessEqual(len(parents), 1,
                                         f"{name} {ied.get('name')} {ln_class}")

    def test_seven_ieds_subscribe_to_goose_and_hold_no_supervision(self):
        """The refusal path for a new logical node, in real files: there is
        no sibling to read an `lnType` or an `LDevice` from."""
        orphans = 0
        for name in self.FILES:
            doc = self.corpus(name)
            namespace = self.namespace(doc)
            for ied in self.ieds(doc):
                subscribes = any(
                    ext.get("srcCBName") and ext.get("serviceType") == "GOOSE"
                    for ext in ied.iter(namespace + "ExtRef"))
                if subscribes and not _supervision_lns(doc, ied, "LGOS"):
                    orphans += 1
        self.assertEqual(orphans, 7)

    def test_instantiating_into_a_real_station_inverts_byte_for_byte(self):
        """The whole of A5's guarantee, over 830,000 elements of vendor
        markup: reuse, a fresh logical node, and a blanking, each applied and
        undone."""
        for name in self.FILES:
            doc = self.corpus(name)
            before = doc.to_bytes()
            ied = next((i for i in self.ieds(doc)
                        if _first_free(doc, i, "LGOS") is not None), None)
            if ied is None:
                continue
            block = self.some_unwatched_block(doc, ied)
            sup = Supervision(ied, block)
            free = _first_free(doc, ied, "LGOS")
            undo = doc.apply_edit(instantiate_supervision(doc, sup))
            self.assertEqual(_supervised_reference(doc, free),
                             control_block_obj_ref(doc, block))
            back = doc.apply_edit(remove_supervision(doc, free))
            self.assertEqual(_supervised_reference(doc, free), "")
            doc.apply_edit(back)
            doc.apply_edit(undo)
            self.assertEqual(doc.to_bytes(), before, name)


class TestWiringCorpus(TestCorpus):
    """What the seven flags do against 830,000 elements of vendor markup.

    A14's `TestCorpus` above pins the measurements the MODULE's decisions rest
    on; these pin the ones A14b's decisions rest on, and each of them can
    falsify a decision rather than merely exercise a path.
    """

    def pairs(self, doc):
        """``{(id(subscriber IED), object reference): [ExtRef, ...]}`` for
        every Edition 2 subscription to a GOOSE or sampled-value block."""
        namespace = self.namespace(doc)
        found = {}
        for ied in self.ieds(doc):
            for ext_ref in ied.iter(namespace + "ExtRef"):
                if ext_ref.get("serviceType") not in ("GOOSE", "SMV"):
                    continue
                if not ext_ref.get("srcCBName"):
                    continue
                block = source_control_block(doc, ext_ref)
                if block is None:
                    continue
                reference = control_block_obj_ref(doc, block)
                if reference:
                    found.setdefault((id(ied), ied, reference, block),
                                     []).append(ext_ref)
        return found

    def test_every_unsupervised_subscription_refuses_for_one_reason(self):
        """**16 of 16, and all on the seven IEDs that hold no supervision node
        at all.** This is the measurement the refusal decision turns on: with
        `ignore_supervision=False`, `subscribe` cannot close a single one of
        the corpus's supervision gaps, because closing one needs an `LNodeType`
        that `importLNodeType` has not been written to import yet.

        If this ever drops below 16, either the corpus changed or a new
        logical node is being built where A14 said it could not be -- and both
        are things a reader of Q33 should be told about loudly.
        """
        refused = supervised = 0
        for name in self.FILES:
            doc = self.corpus(name)
            for (_key, ied, reference, block), _refs in self.pairs(doc).items():
                ln_class = supervision_ln_class(block)
                if _watching(doc, ied, ln_class, reference) is not None:
                    supervised += 1
                    continue
                self.assertEqual(_supervision_lns(doc, ied, ln_class), [],
                                 f"{name} {ied.get('name')} {reference}")
                self.assertFalse(can_instantiate_supervision(
                    doc, Supervision(ied, block)))
                refused += 1
        self.assertEqual((supervised, refused), (548, 16))

    def test_no_supervision_survives_without_a_subscription(self):
        """**0 of 549**, and it is why the two readings of "remove the
        supervision of a removed control block" cannot be told apart by any
        file we have.

        `remove_control_block` sweeps by the BLOCK and `unsubscribe` by the
        `ExtRef`s; the block sweep is a superset, and this is the measurement
        saying the extra is empty here. A13's `remove_ied` is what breaks the
        tie, not the corpus. Q33.
        """
        orphans = 0
        for name in self.FILES:
            doc = self.corpus(name)
            subscribed_refs = {reference for (_k, _ied, reference, _b)
                               in self.pairs(doc)}
            for ied in self.ieds(doc):
                for node in _supervision_lns(doc, ied):
                    reference = _supervised_reference(doc, node)
                    if reference and reference not in subscribed_refs:
                        orphans += 1
        self.assertEqual(orphans, 0)

    def test_free_slots_are_the_exception_across_the_corpus(self):
        """**45 of 59 (IED, class) blocks hold no free slot at all**, which is
        why `new_supervision_ln` had to be reachable from `subscribe` and why
        its default still had to be off -- one number arguing both halves.

        **8 hold exactly one**, which is the shape that makes the batch claim
        a correctness fix rather than tidiness: two supervisions planned in
        one call against one free slot silently lose one of the two.
        """
        histogram = {}
        for name in self.FILES:
            doc = self.corpus(name)
            for ied in self.ieds(doc):
                for ln_class in ("LGOS", "LSVS"):
                    nodes = _supervision_lns(doc, ied, ln_class)
                    if not nodes:
                        continue
                    free = sum(1 for node in nodes
                               if not _supervised_reference(doc, node))
                    histogram[free] = histogram.get(free, 0) + 1
        self.assertEqual(histogram.get(0), 45)
        self.assertEqual(histogram.get(1), 8)
        self.assertEqual(sum(histogram.values()), 59)

    def subscribers_in(self, doc, ied, block):
        """Every `ExtRef` of ``ied`` bound to ``block``, by A9's definition.

        **Which is wider than `serviceType` suggests, and the corpus is why
        this is spelled out.** `QPC1_LT1_UPC1` takes `QPC1_TFE_UPC1CFG/LLN0.
        GoSB00` through six `ExtRef` elements, and one of them names the
        control block with `pServT="GOOSE"` and no `serviceType`, no `doName`
        and no `daName` -- a subscription to the block rather than to an
        attribute of it. :func:`~py61850.scl.find_control_block_subscription`
        counts it and so does the last-`ExtRef` rule, because the input is
        still bound to that publisher until somebody unbinds it. Counting
        only `serviceType` inputs would blank a supervision while one
        subscription was still standing.
        """
        return [ext_ref for ext_ref
                in find_control_block_subscription(doc, block)
                if _ied_of(doc, ext_ref) is ied]

    def test_unsubscribing_all_but_one_extref_leaves_supervision_standing(self):
        """The last-`ExtRef` rule on a real station, from both sides.

        The pair chosen is the first supervised one carrying more than one
        `ExtRef` -- 531 of the corpus's 566 are that shape, so "the last one"
        is a question about a set almost everywhere in these files.
        """
        checked = 0
        for name in self.FILES:
            doc = self.corpus(name)
            for (_k, ied, reference, block), _some in self.pairs(doc).items():
                refs = self.subscribers_in(doc, ied, block)
                if len(refs) < 2:
                    continue
                node = _watching(doc, ied, supervision_ln_class(block),
                                 reference)
                if node is None:
                    continue
                kept = unsubscribe(doc, refs[:-1], ignore_supervision=False)
                self.assertEqual(
                    [edit for edit in kept
                     if isinstance(edit, SetTextContent)], [], name)
                whole = unsubscribe(doc, refs, ignore_supervision=False)
                blanks = [edit for edit in whole
                          if isinstance(edit, SetTextContent)]
                self.assertEqual(len(blanks), 1, name)
                self.assertEqual(_supervised_reference(doc, node), reference)
                doc.apply_edit(whole)
                self.assertEqual(_supervised_reference(doc, node), "", name)
                checked += 1
                break
        self.assertEqual(checked, len(self.FILES))

    def test_an_unsubscribe_with_supervision_inverts_byte_for_byte(self):
        """A5's guarantee over the whole compound edit this phase adds: the
        unbinding and the blanking applied and undone together."""
        for name in self.FILES:
            doc = self.corpus(name)
            before = doc.to_bytes()
            for (_k, ied, reference, block), _some in self.pairs(doc).items():
                if _watching(doc, ied, supervision_ln_class(block),
                             reference) is None:
                    continue
                refs = self.subscribers_in(doc, ied, block)
                undo = doc.apply_edit(
                    unsubscribe(doc, refs, ignore_supervision=False))
                self.assertNotEqual(doc.to_bytes(), before)
                doc.apply_edit(undo)
                self.assertEqual(doc.to_bytes(), before, name)
                break


if __name__ == "__main__":
    unittest.main()

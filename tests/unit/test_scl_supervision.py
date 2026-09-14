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
    EditRejected,
    Insert,
    LN_INST_RANGE,
    Remove,
    SclDocument,
    SetTextContent,
    Supervision,
    can_instantiate_supervision,
    can_remove_supervision,
    control_block_obj_ref,
    instantiate_supervision,
    is_src_ref_editable,
    iter_local,
    max_supervision,
    remove_supervision,
    supervision_ln_class,
)
from py61850.scl.supervision import (
    _bound_supervisions,
    _first_free,
    _supervised_reference,
    _supervision_lns,
)
from tests.unit import roundtrip
from tests.unit import scl_fixtures as fx

PUB_CB_REF = "PUBLD/LLN0.GC1"
PUB_SV_REF = "PUBLD/LLN0.SV1"


def publisher(name="PUB"):
    """One IED publishing a GOOSE block and a sampled-value block."""
    body = fx.dataset("DS1", (fx.fcda("LD", "MMXU", "TotW", "MX"),))
    body += fx.gse_control("GC1", dat_set="DS1", app_id="PUB_APP")
    body += fx.smv_control("SV1", dat_set="DS1")
    return fx.ied(name, fx.access_point(
        "S1", fx.ldevice("LD", fx.ln0(body=body))))


def subscriber(name="SUB", nodes=(), services=None, ld_inst="CFG",
               extra_ldevices=""):
    """One IED whose `CFG` logical device holds the supervision nodes given."""
    inner = fx.ldevice(ld_inst, fx.ln0() + "".join(nodes)) + extra_ldevices
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


def with_services(name, nodes, entries):
    """A subscriber IED carrying a `Services` section.

    `Services` goes after `AccessPoint` in `tIED`'s sequence, which is why it
    is spelled here rather than passed to :func:`subscriber`.
    """
    return fx.ied(name,
                  fx.access_point("S1", fx.ldevice("CFG", fx.ln0() + "".join(nodes)))
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
        low, high = LN_INST_RANGE
        nodes = tuple(fx.supervision(inst=str(i), cb_ref=f"X/LLN0.C{i}")
                      for i in range(low, high + 1))
        doc = self.station(publisher(), subscriber(nodes=nodes))
        sup = Supervision(ied_of(doc, "SUB"),
                          find(doc, "GSEControl", name="GC1"))
        with self.assertRaises(EditRejected) as caught:
            instantiate_supervision(doc, sup, new_supervision_ln=True)
        self.assertIn("whole range", str(caught.exception))

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


if __name__ == "__main__":
    unittest.main()

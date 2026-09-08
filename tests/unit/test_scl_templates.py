# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""DataTypeTemplates: the station-wide type pool every instance resolves
through."""

import tempfile
import unittest

from py61850.scl.document import SclDocument
from tests.unit import scl_fixtures as fx


def _doc(tmpdir, *sections):
    return SclDocument.parse(fx.write(tmpdir, "t.scd", fx.scl(*sections)))


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name


class TestPool(_Base):
    def test_lnode_type_resolves_to_its_data_objects(self):
        d = _doc(self.tmpdir, fx.templates(
            fx.lnode_type("LN_A", "CSWI", dos=[("Pos", "DO_Pos")]),
            fx.do_type("DO_Pos", cdc="DPC",
                       das=[{"name": "stVal", "fc": "ST", "bType": "Dbpos"}]),
        ))
        lnt = d.templates.lnode_type("LN_A")
        self.assertEqual(lnt.ln_class, "CSWI")
        self.assertEqual(lnt.objects, {"Pos": "DO_Pos"})

    def test_do_type_carries_its_cdc_and_attributes(self):
        d = _doc(self.tmpdir, fx.templates(
            fx.do_type("DO_Pos", cdc="DPC",
                       das=[{"name": "stVal", "fc": "ST", "bType": "Dbpos"},
                            {"name": "q", "fc": "ST", "bType": "Quality"}]),
        ))
        dot = d.templates.do_type("DO_Pos")
        self.assertEqual(dot.cdc, "DPC")
        self.assertEqual(sorted(dot.attributes), ["q", "stVal"])
        self.assertEqual(dot.attributes["stVal"].fc, "ST")
        self.assertEqual(dot.attributes["stVal"].btype, "Dbpos")

    def test_an_unknown_id_is_none_not_an_error(self):
        # A file may reference a type it does not carry -- an ICD trimmed by
        # a tool, a hand-edited SCD. That is a fact to report upward, not a
        # reason to fail the whole document.
        d = _doc(self.tmpdir, fx.templates())
        self.assertIsNone(d.templates.lnode_type("NOPE"))
        self.assertIsNone(d.templates.do_type("NOPE"))

    def test_the_pool_is_built_once(self):
        d = _doc(self.tmpdir, fx.templates(fx.do_type("D")))
        self.assertIs(d.templates, d.templates)


class TestEnums(_Base):
    def test_enum_values_are_resolved_onto_the_attribute(self):
        # This is what replaces guessing from a DO's name: the file says
        # which values exist and what they mean.
        d = _doc(self.tmpdir, fx.templates(
            fx.do_type("DO_Health", cdc="ENS",
                       das=[{"name": "stVal", "fc": "ST", "bType": "Enum",
                             "type": "HealthKind"}]),
            fx.enum_type("HealthKind",
                         values=[(1, "Ok"), (2, "Warning"), (3, "Alarm")]),
        ))
        attr = d.templates.do_type("DO_Health").attributes["stVal"]
        self.assertEqual(attr.btype, "Enum")
        self.assertEqual(attr.enum_values, {1: "Ok", 2: "Warning", 3: "Alarm"})

    def test_a_non_enum_attribute_has_no_enum_values(self):
        d = _doc(self.tmpdir, fx.templates(
            fx.do_type("D", das=[{"name": "stVal", "fc": "ST",
                                  "bType": "BOOLEAN"}])))
        self.assertIsNone(d.templates.do_type("D").attributes["stVal"].enum_values)

    def test_a_dangling_enum_reference_leaves_the_values_unknown(self):
        d = _doc(self.tmpdir, fx.templates(
            fx.do_type("D", das=[{"name": "stVal", "fc": "ST",
                                  "bType": "Enum", "type": "Missing"}])))
        self.assertIsNone(d.templates.do_type("D").attributes["stVal"].enum_values)


class TestStructs(_Base):
    def test_a_struct_attribute_expands_through_its_da_type(self):
        d = _doc(self.tmpdir, fx.templates(
            fx.do_type("DO_Ctl", cdc="SPC",
                       das=[{"name": "Oper", "fc": "CO", "bType": "Struct",
                             "type": "OperSPC"}]),
            fx.da_type("OperSPC", bdas=[{"name": "ctlVal", "bType": "BOOLEAN"},
                                        {"name": "T", "bType": "Timestamp"}]),
        ))
        oper = d.templates.do_type("DO_Ctl").attributes["Oper"]
        self.assertEqual(oper.btype, "Struct")
        self.assertEqual(sorted(oper.sub_specs), ["T", "ctlVal"])
        self.assertEqual(oper.sub_specs["ctlVal"].btype, "BOOLEAN")

    def test_a_sub_attribute_inherits_the_root_functional_constraint(self):
        # IEC 61850 puts the FC on the root DA; everything descending inside
        # it comes along. Oper.ctlVal is CO because Oper is CO.
        d = _doc(self.tmpdir, fx.templates(
            fx.do_type("DO_Ctl", cdc="SPC",
                       das=[{"name": "Oper", "fc": "CO", "bType": "Struct",
                             "type": "OperSPC"}]),
            fx.da_type("OperSPC", bdas=[{"name": "ctlVal", "bType": "BOOLEAN"}]),
        ))
        oper = d.templates.do_type("DO_Ctl").attributes["Oper"]
        self.assertEqual(oper.sub_specs["ctlVal"].fc, "CO")

    def test_sub_data_objects_are_recorded(self):
        d = _doc(self.tmpdir, fx.templates(
            fx.do_type("DO_WYE", cdc="WYE", sdos=[("phsA", "DO_CMV")]),
            fx.do_type("DO_CMV", cdc="CMV"),
        ))
        self.assertEqual(d.templates.do_type("DO_WYE").sub_objects,
                         {"phsA": "DO_CMV"})

    def test_a_self_referencing_da_type_does_not_recurse_for_ever(self):
        # Defensive: a malformed file can point a DAType at itself. The pool
        # must return, not exhaust the stack.
        d = _doc(self.tmpdir, fx.templates(
            fx.do_type("D", das=[{"name": "a", "fc": "ST", "bType": "Struct",
                                  "type": "Loop"}]),
            fx.da_type("Loop", bdas=[{"name": "b", "bType": "Struct",
                                      "type": "Loop"}]),
        ))
        attr = d.templates.do_type("D").attributes["a"]
        self.assertIn("b", attr.sub_specs)
        self.assertEqual(attr.sub_specs["b"].sub_specs, {})


if __name__ == "__main__":
    unittest.main()

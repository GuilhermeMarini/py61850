"""The pure LN-name handling behind MmsClient.get_logical_nodes -- no client,
no transport: parsing a name and filtering the result are plain functions."""

import re
import unittest

from py61850 import LogicalNode
from py61850.mms.services.directory import (
    compile_ln_filter, logical_nodes_from_names, parse_ln_name,
)


class TestParseLnName(unittest.TestCase):
    def test_prefix_class_instance(self):
        self.assertEqual(parse_ln_name("ACN1GGIO1"), ("ACN1", "GGIO", "1"))
        self.assertEqual(parse_ln_name("DevIDLPHD1"), ("DevID", "LPHD", "1"))
        self.assertEqual(parse_ln_name("G1PTOC1"), ("G1", "PTOC", "1"))

    def test_no_prefix(self):
        self.assertEqual(parse_ln_name("LPHD1"), ("", "LPHD", "1"))

    def test_lln0_has_no_instance(self):
        self.assertEqual(parse_ln_name("LLN0"), ("", "LLN0", ""))

    def test_multi_digit_instance(self):
        self.assertEqual(parse_ln_name("S10PTOC12"), ("S10", "PTOC", "12"))

    def test_prefix_may_contain_digits_and_underscores(self):
        """A real one off an SEL-411L: prefix 'MYLD_UP', class LGOS."""
        self.assertEqual(parse_ln_name("MYLD_UPLGOS3"),
                         ("MYLD_UP", "LGOS", "3"))

    def test_a_name_too_short_to_split_is_all_class(self):
        self.assertEqual(parse_ln_name("XCB"), ("", "XCB", ""))


class TestLogicalNode(unittest.TestCase):
    def test_reference_and_parts(self):
        ln = LogicalNode("MYLD_PROT", "G1PTOC1")
        self.assertEqual(ln.ref, "MYLD_PROT/G1PTOC1")
        self.assertEqual((ln.prefix, ln.ln_class, ln.instance), ("G1", "PTOC", "1"))

    def test_identity_is_the_reference(self):
        self.assertEqual(LogicalNode("LD0", "LLN0"), LogicalNode("LD0", "LLN0"))
        self.assertNotEqual(LogicalNode("LD0", "LLN0"), LogicalNode("LD1", "LLN0"))
        self.assertEqual(len({LogicalNode("LD0", "LLN0"),
                              LogicalNode("LD0", "LLN0")}), 1)


class TestLogicalNodesFromNames(unittest.TestCase):
    def test_first_component_deduplicated_in_server_order(self):
        nodes = logical_nodes_from_names("LD0", [
            "MET3PMMXU1$MX$A", "MET3PMMXU1$MX$PPV", "LLN0", "LLN0$ST$Beh$stVal"])
        self.assertEqual([n.name for n in nodes], ["MET3PMMXU1", "LLN0"])

    def test_empty_list(self):
        self.assertEqual(logical_nodes_from_names("LD0", []), [])


class TestCompileLnFilter(unittest.TestCase):
    NODES = [LogicalNode("LD0", n) for n in
             ("LLN0", "G1PTOC1", "G2PTOC1", "MET3PMMXU1", "ACN1GGIO1")]

    def names(self, **kw):
        m = compile_ln_filter(**kw)
        return [n.name for n in self.NODES if m(n)]

    def test_no_filter_matches_every_node(self):
        self.assertEqual(len(self.names()), len(self.NODES))

    def test_by_class_ignoring_case(self):
        self.assertEqual(self.names(ln_class="ptoc"), ["G1PTOC1", "G2PTOC1"])
        self.assertEqual(self.names(ln_class=["MMXU", "GGIO"]),
                         ["MET3PMMXU1", "ACN1GGIO1"])

    def test_class_is_exact_not_a_substring(self):
        self.assertEqual(self.names(ln_class="PTO"), [])

    def test_by_glob_on_the_node_name(self):
        self.assertEqual(self.names(pattern="G?PTOC*"), ["G1PTOC1", "G2PTOC1"])

    def test_by_regex_on_the_reference(self):
        self.assertEqual(self.names(regex=r"^LD0/MET"), ["MET3PMMXU1"])
        self.assertEqual(self.names(regex=re.compile(r"GGIO\d+$")), ["ACN1GGIO1"])

    def test_kinds_are_anded(self):
        self.assertEqual(self.names(ln_class="PTOC", pattern="G1*"), ["G1PTOC1"])
        self.assertEqual(self.names(ln_class="PTOC", pattern="MET*"), [])

    def test_case_sensitivity_is_optional(self):
        self.assertEqual(self.names(ln_class="ptoc", ignore_case=False), [])


if __name__ == "__main__":
    unittest.main()

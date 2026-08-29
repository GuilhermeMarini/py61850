"""The pure name matching behind FileTransfer.search_files -- no client, no
transport: :func:`compile_file_filter` and the two name helpers are plain
functions over strings."""

import unittest

from py61850 import folder_of
from py61850.mms.services.files import (
    basename, compile_file_filter, looks_like_directory,
)
from py61850.mms.pdu import DirEntry


class TestNameHelpers(unittest.TestCase):
    def test_folder_of(self):
        self.assertEqual(folder_of("/EVENTS/C4.CFG"), "/EVENTS/")
        self.assertEqual(folder_of("/A/B/C/x"), "/A/B/C/")
        self.assertEqual(folder_of("/CFG.TXT"), "/")
        self.assertEqual(folder_of("CFG.TXT"), "/")

    def test_folder_of_handles_backslash_relays(self):
        self.assertEqual(folder_of("\\COMTRADE\\R1.CFG"), "\\COMTRADE\\")
        self.assertEqual(basename("\\COMTRADE\\R1.CFG"), "R1.CFG")

    def test_looks_like_directory(self):
        self.assertTrue(looks_like_directory(DirEntry("/EVENTS/", 0, None)))
        self.assertTrue(looks_like_directory(DirEntry("\\EVENTS\\", 0, None)))
        self.assertFalse(looks_like_directory(DirEntry("/EVENTS/C4.CFG", 1, None)))


class TestCompileFileFilter(unittest.TestCase):
    def test_no_filter_matches_everything(self):
        self.assertTrue(compile_file_filter()("/anything"))

    def test_extensions_take_an_optional_dot(self):
        m = compile_file_filter(extensions=["cfg", ".dat", "HDR"])
        for name in ("/EVENTS/C4.CFG", "/a.dat", "/x.hdr"):
            self.assertTrue(m(name), name)
        self.assertFalse(m("/a.txt"))

    def test_extension_is_not_a_bare_suffix(self):
        """'.cfg' must not match a file merely ending in the letters cfg."""
        self.assertFalse(compile_file_filter(extensions=["cfg"])("/x.mycfg"))

    def test_glob_matches_the_file_name_alone(self):
        m = compile_file_filter("C4_*.TXT")
        self.assertTrue(m("/EVENTS/C4_10117.TXT"))
        self.assertFalse(m("/EVENTS/C5_10117.TXT"))

    def test_glob_with_a_separator_matches_the_whole_path(self):
        m = compile_file_filter("/EVENTS/*.CFG")
        self.assertTrue(m("/EVENTS/C4.CFG"))
        self.assertFalse(m("/OLD/C4.CFG"))

    def test_several_globs_are_ored(self):
        m = compile_file_filter(["*.cfg", "*.dat"])
        self.assertTrue(m("/a.cfg"))
        self.assertTrue(m("/a.dat"))
        self.assertFalse(m("/a.hdr"))

    def test_kinds_are_anded(self):
        m = compile_file_filter(extensions=["cfg"], regex="EVENTS")
        self.assertTrue(m("/EVENTS/C4.CFG"))
        self.assertFalse(m("/OLD/C4.CFG"))          # right type, wrong folder
        self.assertFalse(m("/EVENTS/C4.TXT"))       # right folder, wrong type

    def test_case_sensitivity_is_optional(self):
        self.assertTrue(compile_file_filter("*.cfg")("/A.CFG"))
        self.assertFalse(compile_file_filter("*.cfg", ignore_case=False)("/A.CFG"))
        self.assertFalse(compile_file_filter(regex="events", ignore_case=False)
                         ("/EVENTS/C4.CFG"))

    def test_a_compiled_regex_is_accepted(self):
        import re
        self.assertTrue(compile_file_filter(regex=re.compile(r"\d{5}"))("/C4_10117.TXT"))


if __name__ == "__main__":
    unittest.main()

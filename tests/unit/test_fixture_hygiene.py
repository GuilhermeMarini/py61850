# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The tracked SCD corpus carries no substation's identity.

Three of the four fixtures are real vendor exports. They are here because a
round-trip test is worth nothing against a file we wrote ourselves -- only a
real DIGSI or SEL Architect export has the formatting quirks the guarantee is
about. What they must not carry is the substation they came from.

**These checks match shapes, never names.** A test asserting that some
particular substation, utility or engineer does not appear would have to
write that name down, in a public repository, which is precisely what the
anonymisation was for. So the rules below say what an anonymised file looks
like -- accounts are `user<n>`, machines are `PC<n>`, addressing is in the
documentation ranges -- and anything else fails.

The complementary check, that each specific real name was substituted, is
`tools/anonymise_scd.py --check`. It can name them because it reads the
mapping, and the mapping is not tracked.
"""

import pathlib
import re
import unittest

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "scl"

# A Windows account name, wherever one can appear: as a path segment under
# `C:\Users\`, and as the operator recorded on a DIGSI history entry.
ACCOUNT = re.compile(r"^user\d+$")
# A machine name, as DIGSI writes it into `Hitem/@who`.
MACHINE = re.compile(r"^PC\d+$")
# `Hitem/@who` reads "Licenced User: <vendor?> <account> Machine: <machine> User: <account>".
# The vendor half is a tool string, not an identity, and is kept deliberately.
LICENCED = re.compile(r"^\s*(?:Siemens AG\s+)?user\d+\s*$")

# Addressing that belongs to a real substation. The documentation ranges of
# RFC 5737 are what the anonymiser maps onto, so anything private that
# survives is a leak. Version strings such as "3.2.0.28" and the subnet mask
# 255.255.255.0 are dotted quads too, which is why this matches the private
# ranges rather than "anything shaped like an address".
PRIVATE_IP = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3})\b"
)

USER_PATH = re.compile(r"C:\\Users\\([^\\\"<]+)")
WHO_MACHINE = re.compile(r"Machine: ([^ \"]+) User: ([^\"]*)")
WHO_LICENCED = re.compile(r"Licenced User:([^\"]*?)Machine:")


# The corpus is excluded from the sdist -- 44 MB of vendor SCDs is a
# development artefact, not something an installer needs -- so a suite run
# from an unpacked sdist has the directory and its README but no `.scd`.
# That is the one legitimate reason for these checks to have nothing to read,
# and it is why the directory itself is asserted separately: a renamed or
# moved corpus must fail, an absent one must skip.
_CORPUS = sorted(FIXTURES.glob("*.scd"))


class FixtureHygieneTests(unittest.TestCase):
    """Every rule here is about identity, not about SCL."""

    @classmethod
    def setUpClass(cls):
        cls.corpus = _CORPUS

    def test_the_corpus_directory_is_where_it_is_expected(self):
        # Without this, moving the directory turns every check below into a
        # loop over nothing that passes by saying nothing.
        self.assertTrue(FIXTURES.is_dir(), f"corpus directory missing: {FIXTURES}")

    @unittest.skipUnless(_CORPUS, "corpus not present (sdist install)")
    def test_windows_accounts_are_placeholders(self):
        for path in self.corpus:
            text = path.read_text(encoding="utf-8")
            for segment in set(USER_PATH.findall(text)):
                self.assertRegex(segment, ACCOUNT,
                                 f"{path.name}: real account in a C:\\Users path")

    @unittest.skipUnless(_CORPUS, "corpus not present (sdist install)")
    def test_history_entries_name_no_person_and_no_machine(self):
        for path in self.corpus:
            text = path.read_text(encoding="utf-8")
            for machine, account in set(WHO_MACHINE.findall(text)):
                self.assertRegex(machine, MACHINE, f"{path.name}: real machine name")
                self.assertRegex(account, ACCOUNT, f"{path.name}: real account name")
            for licenced in set(WHO_LICENCED.findall(text)):
                self.assertRegex(licenced, LICENCED,
                                 f"{path.name}: real licensee in Hitem/@who")

    @unittest.skipUnless(_CORPUS, "corpus not present (sdist install)")
    def test_addressing_is_in_the_documentation_ranges(self):
        for path in self.corpus:
            text = path.read_text(encoding="utf-8")
            found = sorted(set(PRIVATE_IP.findall(text)))
            self.assertEqual([], found,
                             f"{path.name}: private addressing survived anonymisation")


if __name__ == "__main__":
    unittest.main()

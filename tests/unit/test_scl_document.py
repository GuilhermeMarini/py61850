# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The SCL document itself: reading it, refusing it, and the shallow facts
that need no instance model."""

import tempfile
import unittest

from py61850.scl import document as doc_mod
from py61850.scl._xmlsafe import DtdNotAllowed
from tests.unit import scl_fixtures as fx

SclDocument = doc_mod.SclDocument


class _TmpMixin(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name

    def doc(self, text, name="t.scd"):
        return SclDocument.parse(fx.write(self.tmpdir, name, text))


class TestConstructors(_TmpMixin):
    def test_parse_reads_a_document(self):
        d = self.doc(fx.scl(fx.header(), fx.ied("IED1")))
        self.assertEqual(doc_mod.strip_ns(d.root.tag), "SCL")

    def test_parse_raises_on_a_missing_file(self):
        with self.assertRaises(OSError):
            SclDocument.parse(self.tmpdir + "/nope.scd")

    def test_parse_raises_on_a_dtd(self):
        text = ('<?xml version="1.0"?>\n<!DOCTYPE SCL [\n'
                '  <!ENTITY a "AAAA">\n]>\n<SCL/>\n')
        with self.assertRaises(DtdNotAllowed):
            self.doc(text)

    def test_load_returns_none_instead_of_raising(self):
        # `load` is for a file a USER supplied: a web tool must be able to
        # hand it whatever was uploaded and get an answer, not a traceback.
        self.assertIsNone(SclDocument.load(self.tmpdir + "/nope.scd"))
        bad = fx.write(self.tmpdir, "bad.scd", "<SCL><unclosed>")
        self.assertIsNone(SclDocument.load(bad))

    def test_load_returns_a_document_for_a_good_file(self):
        path = fx.write(self.tmpdir, "ok.scd", fx.scl(fx.header()))
        self.assertIsNotNone(SclDocument.load(path))


class TestNamespaceAgnostic(_TmpMixin):
    def test_a_document_declaring_no_namespace_still_reads(self):
        # Hand-made SCDs that declare no namespace are real and must work.
        d = self.doc(fx.scl(fx.header(), fx.ied("IED1"), ns=False))
        self.assertEqual([e.get("name") for e in
                          doc_mod.iter_local(d.root, "IED")], ["IED1"])

    def test_the_namespaced_form_reads_identically(self):
        d = self.doc(fx.scl(fx.header(), fx.ied("IED1"), ns=True))
        self.assertEqual([e.get("name") for e in
                          doc_mod.iter_local(d.root, "IED")], ["IED1"])

    def test_every_declared_namespace_is_reported(self):
        # A real station file declares vendor namespaces beside the standard
        # one -- the SEL namespace appears in all three reference SCDs,
        # including the Siemens station's.
        text = fx.scl(fx.header()).replace(
            "<SCL ", '<SCL xmlns:esel="http://www.selinc.com/2006/61850" ')
        d = self.doc(text)
        self.assertIn("http://www.iec.ch/61850/2003/SCL", d.namespaces)
        self.assertIn("http://www.selinc.com/2006/61850", d.namespaces)


class TestHeader(_TmpMixin):
    def test_header_fields(self):
        d = self.doc(fx.scl(fx.header(id_="Station1", version="2007",
                                      revision="B", tool_id="DIGSI")))
        self.assertEqual(d.header.id, "Station1")
        self.assertEqual(d.header.version, "2007")
        self.assertEqual(d.header.revision, "B")
        self.assertEqual(d.header.tool_id, "DIGSI")
        self.assertEqual(d.header.name_structure, "IEDName")

    def test_a_document_with_no_header_gives_none(self):
        self.assertIsNone(self.doc(fx.scl(fx.ied("IED1"))).header)

    def test_a_private_header_lookalike_does_not_shadow_the_real_one(self):
        # Same class of bug commit 671bb35 fixed for <IED>: a vendor Private
        # is free to nest an element sharing any local name, <Header>
        # included, and it may sit before the real element in document
        # order -- teste_siemens.scd has five root-level <Private> elements
        # ahead of its <Header>.
        decoy = fx.private("Vendor-Thing",
                           '<Header id="DECOY" version="9" revision="9"/>')
        real = fx.header(id_="REAL", version="1", revision="0")
        d = self.doc(fx.scl(decoy, real))
        self.assertEqual(d.header.id, "REAL")


class TestPrivates(_TmpMixin):
    def test_document_level_privates_are_grouped_by_type(self):
        d = self.doc(fx.scl(fx.private("SEL_IedInfo", "a"),
                            fx.private("SEL_IedInfo", "b"),
                            fx.private("Siemens-IED-Id", "c")))
        self.assertEqual(sorted(d.privates), ["SEL_IedInfo", "Siemens-IED-Id"])
        self.assertEqual([e.text for e in d.privates["SEL_IedInfo"]], ["a", "b"])

    def test_privates_of_reads_only_direct_children(self):
        # A Private under a nested element belongs to THAT element, not to
        # its ancestors. Collecting them by descent would give every IED the
        # whole station's privates.
        d = self.doc(fx.scl(fx.ied("IED1", body=fx.private("SEL_IedInfo"))))
        self.assertEqual(d.privates, {})
        ied_el = next(doc_mod.iter_local(d.root, "IED"))
        self.assertEqual(sorted(doc_mod.privates_of(ied_el)), ["SEL_IedInfo"])


class TestEdition(_TmpMixin):
    def test_edition_comes_from_the_root_not_the_header(self):
        # Header carries the exporting tool's own bookkeeping -- values that
        # look nothing like an edition on real files (e.g. version="204"
        # revision="1.0" on the SEL reference station). The edition lives on
        # the <SCL> root instead.
        d = self.doc(fx.scl(fx.header(version="204", revision="1.0"),
                            version="2007", revision="B", release="4"))
        self.assertEqual(d.edition, "2007B")

    def test_release_is_exposed_separately(self):
        d = self.doc(fx.scl(fx.header(), version="2007", revision="B",
                            release="4"))
        self.assertEqual(d.release, "4")

    def test_edition_2_and_edition_2_1_are_distinguishable(self):
        # "2007B" alone is the same string for both -- release is what tells
        # Edition 2 (release 3) apart from Edition 2.1 (release 4).
        ed2 = self.doc(fx.scl(fx.header(), version="2007", revision="B",
                              release="3"))
        ed2_1 = self.doc(fx.scl(fx.header(), version="2007", revision="B",
                                release="4"))
        self.assertEqual(ed2.edition, ed2_1.edition)
        self.assertNotEqual(ed2.release, ed2_1.release)
        self.assertEqual(ed2.release, "3")
        self.assertEqual(ed2_1.release, "4")

    def test_edition_is_none_without_a_root_version(self):
        # A header carrying edition-shaped values does not count: the root
        # is the only place this reads from.
        d = self.doc(fx.scl(fx.header(version="2007", revision="B")))
        self.assertIsNone(d.edition)

    def test_release_is_none_without_one(self):
        d = self.doc(fx.scl(fx.header(), version="2007", revision="B"))
        self.assertIsNone(d.release)


if __name__ == "__main__":
    unittest.main()

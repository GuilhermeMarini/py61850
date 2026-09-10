# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""A document is written out under its own namespace prefixes, and nothing else is.

The round-trip corpus already proves the first half against four real files:
every `xmlns` declaration comes back, under the prefix the file wrote it with,
including the ones nothing uses. What it cannot prove is the SECOND half --
that the mechanism which does that is contained -- because it serialises one
document at a time and never asks what the process looks like afterwards.

## Why containment needs a test of its own

`ET.register_namespace` is the only lever `ElementTree` offers, and it writes
into a dict shared by every document in the process. Worse than shared: it
DELETES every existing entry matching either the URI or the prefix, so
registering is not additive.

**The damage is not where it first looks.** Registering and never restoring
would NOT usually corrupt this package's own output, because every
serialisation re-registers its own declarations immediately before writing --
that was measured, by running these cases against a deliberately leaking
version, and it is why the obvious two-document case is filed under fidelity
below and not here. The two things a leak really breaks are:

1. **Every other `ElementTree` user in the process.** Not a consumer of this
   package at all -- any code that serialises an element in a URI some SCD
   happened to declare gets that SCD's prefix.
2. **A document that declares nothing this scanner can see** yet contains a
   qualified element -- one whose declaration is past the 64 kB head scan, or
   which inherits a namespace some other way. It has no declarations of its
   own to re-register, so it inherits a stranger's.

Both are silent, and both reach a file that goes back to DIGSI or SEL
Architect. `pac-ct` is the caller that makes it concrete rather than
theoretical: it serves SCL tools from a threaded HTTP server, so two
documents are not merely in one process, they are in one process at the same
time.

## The refusals

Three shapes cannot be reproduced and are dropped rather than mishandled --
`document._document_prefixes` says why each. None is in the corpus, so this is
where they are written down: a reserved `nsN` prefix, a prefix already taken
by a used namespace, and the same URI declared twice. The last one is the
only one of the three that loses nothing at all.
"""

import tempfile
import unittest
from xml.etree import ElementTree as ET

from py61850.scl import SclDocument
from py61850.scl import document as doc_mod

from . import scl_fixtures as fx

SCL = "http://www.iec.ch/61850/2003/SCL"
SEL = "http://www.selinc.com/2006/61850"
SXY = "http://www.iec.ch/61850/2003/SCLcoordinates"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


def _root(decls, body="", attrs=""):
    """An `<SCL>` document declaring `decls`, as `(prefix, uri)` pairs.

    Written by hand rather than through `scl_fixtures.scl`, because what is
    under test here is the exact spelling of the root's declarations and that
    helper deliberately offers only "namespace or no namespace".
    """
    spelled = " ".join(
        f'xmlns:{p}="{u}"' if p else f'xmlns="{u}"' for p, u in decls)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<SCL {spelled}{attrs}>\n{body}\n</SCL>\n')


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name
        self._n = 0

    def doc(self, text):
        self._n += 1
        return SclDocument.parse(fx.write(self.tmpdir, f"t{self._n}.scd", text))

    def out(self, text):
        """The document's own serialisation, as text."""
        return self.doc(text)._to_bytes().decode("utf-8")


class TestDeclarationsSurvive(_Base):
    """Both halves of the fidelity guarantee, on documents small enough to read.

    The corpus states the same things at station scale. These state them at a
    size where the expected output can be written out in full, which is what
    makes a failure here point at a cause rather than at a 23 MB diff.
    """

    def test_a_used_prefix_is_the_documents_own(self):
        out = self.out(_root(
            [("", SCL), ("sxy", SXY)],
            '<Substation name="S1" sxy:x="3" sxy:y="4"/>'))
        self.assertIn('sxy:x="3"', out)
        self.assertIn(f'xmlns:sxy="{SXY}"', out)
        self.assertNotIn("ns0", out)

    def test_the_default_namespace_stays_the_default(self):
        """`xmlns=`, not `ns0:` -- and the tags stay unprefixed.

        `ElementTree`'s `write(default_namespace=...)` would also do this and
        cannot be used: it raises `ValueError` on the first UNQUALIFIED
        attribute, and every attribute in SCL is unqualified. Registering the
        empty prefix is what reaches the same place from inside.
        """
        out = self.out(_root([("", SCL)], '<Substation name="S1"/>'))
        self.assertIn(f'<SCL xmlns="{SCL}"', out)
        self.assertIn('<Substation name="S1"', out)

    def test_an_unused_declaration_is_re_emitted(self):
        """The one gap `register_namespace` cannot close.

        `ElementTree` emits a declaration only for a namespace something is
        IN, so a root declaration nothing uses has to be written as a literal
        attribute instead. SEL's private namespace is exactly this shape in
        two of the four corpus files.
        """
        out = self.out(_root([("", SCL), ("sel", SEL)], "<Substation name=\"S1\"/>"))
        self.assertIn(f'xmlns:sel="{SEL}"', out)

    def test_an_unused_declaration_is_re_emitted_once_per_serialisation(self):
        """Serialising twice does not accumulate the literal attribute.

        The failure this guards against is not a duplicate attribute -- a
        dict cannot hold one -- it is the literal outliving the call and
        being counted as used on the next one.
        """
        d = self.doc(_root([("", SCL), ("sel", SEL)], '<Substation name="S1"/>'))
        self.assertEqual(d._to_bytes(), d._to_bytes())
        self.assertIn(f'xmlns:sel="{SEL}"'.encode(), d._to_bytes())

    def test_two_documents_keep_their_own_prefix_for_one_uri(self):
        """The corpus writes the SEL namespace `esel:`; `namespaces.scd` writes it
        `sel:`. Each has to come back its own way, in either order.
        """
        esel = _root([("", SCL), ("esel", SEL)],
                     '<Substation name="S1"><Private type="t" '
                     'xmlns:esel="%s"><esel:x/></Private></Substation>' % SEL)
        sel = _root([("", SCL), ("sel", SEL)],
                    '<Substation name="S1"><Private type="t" '
                    'xmlns:sel="%s"><sel:x/></Private></Substation>' % SEL)
        first = self.out(esel)
        self.assertIn("<sel:x", self.out(sel))
        self.assertEqual(first, self.out(esel))
        self.assertIn("<esel:x", first)
        self.assertNotIn("<sel:x", first)

    def test_every_declaration_of_a_seven_namespace_root_comes_back(self):
        """The mixed-vendor shape, in miniature: used and unused together."""
        decls = [("", SCL), ("xsi", XSI), ("sxy", SXY), ("sel", SEL)]
        out = self.out(_root(decls, '<Substation name="S1" sxy:x="1"/>'))
        for prefix, uri in decls:
            self.assertIn(
                f'xmlns:{prefix}="{uri}"' if prefix else f'xmlns="{uri}"',
                out, f"{prefix or '(default)'} was not written back")


class TestTheTreeIsNotMutated(_Base):
    """Serialising leaves the parsed tree exactly as the parser built it.

    The literal `xmlns:` attributes are a serialiser trick, not content. If
    they outlived the call, every reader in the package -- and the parent map
    the edit layer builds on top of it -- would see attributes on the root
    that are not attributes of the root.
    """

    def test_no_xmlns_attribute_is_left_on_the_root(self):
        d = self.doc(_root([("", SCL), ("sel", SEL)], '<Substation name="S1"/>'))
        before = dict(d.root.attrib)
        d._to_bytes()
        self.assertEqual(before, dict(d.root.attrib))
        self.assertEqual([], [k for k in d.root.attrib if k.startswith("xmlns")])

    def test_the_root_never_carried_one_to_begin_with(self):
        # Without this the assertion above would also pass on a tree where the
        # attributes were added at parse time and never removed.
        d = self.doc(_root([("", SCL), ("sel", SEL)], '<Substation name="S1"/>'))
        self.assertEqual([], [k for k in d.root.attrib if k.startswith("xmlns")])


class TestOneDocumentDoesNotChangeAnother(_Base):
    """`register_namespace` is global; this package's use of it is not.

    Every case here fails against a version that registers without restoring,
    and that was checked rather than assumed -- see the module docstring for
    what such a version does and does not break.
    """

    def test_a_document_declaring_nothing_inherits_no_prefix(self):
        """The leak's second victim, and the one inside this package.

        A document with no declaration in its first 64 kB has nothing of its
        own to register, so whatever the PREVIOUS document registered would
        still be in force when this one is written. `ns0:` is the correct
        answer for it -- the file said nothing about a prefix -- and the file
        serialised just before it must not be able to change that.
        """
        self.out(_root([("", SCL), ("esel", SEL)],
                       '<Substation name="S1"><Private type="t" '
                       'xmlns:esel="%s"><esel:x/></Private></Substation>' % SEL))
        undeclared = ET.Element("{%s}Private" % SEL)
        self.assertNotIn(b"esel", ET.tostring(undeclared))

    def test_the_registry_is_exactly_what_it_was(self):
        """Restored, not merely reset: the comparison is against the snapshot.

        `_NS_MAP` is `xml.etree`'s own dict, reached by a private name. If a
        future CPython renames it, `document` resolves `None` and this fails
        here rather than silently in a consumer's output file.
        """
        self.assertIsNotNone(doc_mod._NS_MAP, "xml.etree._namespace_map is gone")
        before = dict(doc_mod._NS_MAP)
        self.out(_root([("", SCL), ("esel", SEL), ("sel2", SXY)],
                       '<Substation name="S1"/>'))
        self.assertEqual(before, dict(doc_mod._NS_MAP))

    def test_an_unrelated_serialisation_is_unaffected(self):
        """A plain `ElementTree` call, before and after, spells it the same way.

        This is what a consumer that is not using this package at all would
        see. `ns0:` is the RIGHT answer for a bare `ElementTree` -- nothing
        registered anything -- and it has to stay the answer.
        """
        plain = ET.tostring(ET.Element("{%s}SCL" % SCL))
        self.out(_root([("", SCL)], '<Substation name="S1"/>'))
        self.assertEqual(plain, ET.tostring(ET.Element("{%s}SCL" % SCL)))
        self.assertIn(b"ns0", plain)


class TestTheRefusedShapes(_Base):
    """Three declarations that cannot be reproduced, and are dropped, not faked.

    Each would otherwise produce a root with two attributes of one name, which
    is not XML at all -- an output file no parser would read back is a worse
    failure than a lost prefix. None occurs in the corpus; if one ever does,
    the round-trip test's `namespace_declarations` case is what will say so.
    """

    def test_a_reserved_ns0_prefix_is_not_registered(self):
        """`register_namespace` raises `ValueError` on the `nsN` form.

        And re-emitting it literally could collide with the `nsN` the
        serialiser is about to invent for something else. The output stays
        well-formed; the prefix is not the document's own.
        """
        out = self.out(_root([("", SCL), ("ns0", SEL)],
                             '<Substation name="S1"><Private type="t" '
                             'xmlns:ns0="%s"><ns0:x/></Private></Substation>' % SEL))
        self.assertEqual(1, out.count("<SCL "))
        ET.fromstring(out)      # the point: still parseable

    def test_a_prefix_already_claimed_is_not_re_emitted(self):
        """`xsi` is in `ElementTree`'s registry before this package touches it.

        A document declaring `xmlns:xsi` for something else AND using the real
        XMLSchema-instance namespace would need `xsi` to mean two things on
        one element. It cannot, so the unused one is dropped.
        """
        out = self.out(_root(
            [("", SCL), ("xsi", "urn:not-schema-instance")],
            '<Substation name="S1" xsi:nil="true" xmlns:xsi="%s"/>' % XSI))
        self.assertEqual(1, out.count("xmlns:xsi="))
        self.assertIn(XSI, out)
        ET.fromstring(out)

    def test_one_uri_under_two_prefixes_keeps_both_declarations(self):
        """The refusal that loses nothing: the second prefix is re-emitted.

        Only one of the two can be what the tags are written with, and
        first-seen wins -- that is the one a reader of the original file meets
        first. The other survives as a declaration, which is all it ever was.
        """
        out = self.out(_root([("", SCL), ("a", SEL), ("b", SEL)],
                             '<Substation name="S1"><Private type="t" '
                             'xmlns:a="%s"><a:x/></Private></Substation>' % SEL))
        self.assertIn(f'xmlns:a="{SEL}"', out)
        self.assertIn(f'xmlns:b="{SEL}"', out)
        self.assertIn("<a:x", out)
        ET.fromstring(out)


class TestTheDeclarationScanner(_Base):
    """`_declared_namespaces` and the URI view built on top of it."""

    def test_prefix_and_uri_are_both_recovered(self):
        d = self.doc(_root([("", SCL), ("sxy", SXY), ("sel", SEL)],
                           '<Substation name="S1"/>'))
        self.assertEqual((("", SCL), ("sxy", SXY), ("sel", SEL)),
                         d._declarations)

    def test_namespaces_is_still_uris_in_first_seen_order(self):
        """The public property's contract is unchanged.

        `siemenslib` asks `if NS_SIEDIG in doc.namespaces`, so this is the
        shape a released consumer depends on. The prefixes are private.
        """
        d = self.doc(_root([("", SCL), ("a", SEL), ("b", SEL)],
                           '<Substation name="S1"/>'))
        self.assertEqual((SCL, SEL), d.namespaces)

    def test_a_document_declaring_nothing_serialises(self):
        """Hand-made SCDs with no namespace at all are real -- see `scl_fixtures`."""
        out = self.out('<?xml version="1.0" encoding="UTF-8"?>\n'
                       '<SCL version="2007">\n<Substation name="S1"/>\n</SCL>\n')
        self.assertIn("<SCL version=", out)
        self.assertNotIn("xmlns", out)


class TestTheUsedScanner(unittest.TestCase):
    """`_used_namespace_uris`: what the serialiser will declare on its own."""

    def test_a_tag_and_an_attribute_name_both_count(self):
        root = ET.Element("{%s}SCL" % SCL)
        ET.SubElement(root, "Substation", {"{%s}x" % SXY: "1"})
        self.assertEqual({SCL, SXY}, doc_mod._used_namespace_uris(root))

    def test_a_comment_node_is_not_an_error(self):
        """A comment's `tag` is the factory that made it, not a name.

        The same non-string node `strip_ns` guards. Comments became ordinary
        children when the parser started keeping them, so every walk added
        after that has to tolerate one -- including this walk, which is newer
        than the guard and does not go through `strip_ns`.
        """
        root = ET.Element("{%s}SCL" % SCL)
        root.append(ET.Comment(" a note "))
        self.assertEqual({SCL}, doc_mod._used_namespace_uris(root))


if __name__ == "__main__":
    unittest.main()

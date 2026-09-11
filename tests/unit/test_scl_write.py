# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The public write API: ``SclDocument.to_bytes`` and ``SclDocument.write``.

`test_scl_roundtrip.py` states the fidelity guarantee at station scale, on
44 MB of real vendor exports. This states the parts of it **the corpus cannot
reach**, because all four of its files are ordinary UTF-8 SCDs written by a
tool that agrees with itself:

- a file with no XML declaration at all, and one that declares an encoding
  the output is not in;
- a file that opens with a byte-order mark;
- a file whose last line is not terminated;
- a root whose attributes are written in an order no serialiser would pick;
- a document that was never read from a file, so there is no layout to
  reproduce.

And it states the one property no comparison of bytes can show: that
:meth:`SclDocument.write` either replaces the destination or leaves it
exactly as it was, with nothing dropped beside it either way.

Every fixture here is written as BYTES. A test about how a file is spelled
cannot go through a text-mode write that is free to translate line endings on
the way past.
"""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

from py61850.scl import SclDocument
from py61850.scl import document as doc_mod

SCL = "http://www.iec.ch/61850/2003/SCL"
SEL = "http://www.selinc.com/2006/61850"

# `ElementTree` writes an empty element as `<X />`, with the space. The
# fixtures below are spelled that way so a comparison can be EXACT: the
# spacing is a permitted cosmetic exception, and a test that had to forgive
# one would not be showing that the bytes came back.
HEADER = b'<Header id="H" toolID="t" />'


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self._n = 0

    def file(self, data: bytes) -> Path:
        self._n += 1
        path = self.dir / f"t{self._n}.scd"
        path.write_bytes(data)
        return path

    def out(self, data: bytes) -> bytes:
        """``data`` parsed and written straight back out."""
        return SclDocument.parse(self.file(data)).to_bytes()

    def listing(self):
        return sorted(p.name for p in self.dir.iterdir())


class TestTheProlog(_Base):
    """The XML declaration and the byte-order mark: gone before a tree exists."""

    def test_the_declaration_is_the_files_own_spelling(self):
        """`ElementTree` would write `<?xml version='1.0' encoding='utf-8'?>`.

        Single quotes and a lower-cased name, for every file. Three of the
        four corpus fixtures capitalise `UTF-8` and all four use double
        quotes, so the serialiser's own spelling differs from every real
        file it has been shown.
        """
        source = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
                  b'<SCL xmlns="%s">\n  %s\n</SCL>\n' % (SCL.encode(), HEADER))
        self.assertEqual(source, self.out(source))

    def test_an_unusual_declaration_is_reproduced_whole(self):
        """`standalone`, the spacing and the order are none of the writer's business."""
        source = (b"<?xml  version='1.0'   encoding='UTF-8'  standalone='no' ?>\n"
                  b'<SCL xmlns="%s">\n  %s\n</SCL>\n' % (SCL.encode(), HEADER))
        self.assertEqual(source, self.out(source))

    def test_a_file_with_no_declaration_gets_none(self):
        """Adding one would be an edit to a file nobody asked to change."""
        source = b'<SCL xmlns="%s">\n  %s\n</SCL>\n' % (SCL.encode(), HEADER)
        out = self.out(source)
        self.assertEqual(source, out)
        self.assertNotIn(b"<?xml", out)

    def test_a_non_utf8_encoding_name_is_corrected_and_nothing_else_is(self):
        """The one thing deliberately NOT reproduced -- see `to_bytes`.

        Expat decodes whatever the file declared and the writer emits UTF-8,
        so a declaration still naming `ISO-8859-1` would describe the bytes
        wrongly and the next tool to open the file would read mojibake or
        fail. The NAME is replaced; the single quotes around it, the version
        and the spacing are left as they were.
        """
        source = ("<?xml version='1.0' encoding='ISO-8859-1'?>\n"
                  '<SCL xmlns="%s">\n  <Header id="Subestação" />\n</SCL>\n'
                  % SCL).encode("iso-8859-1")
        out = self.out(source)
        self.assertIn(b"<?xml version='1.0' encoding='utf-8'?>", out)
        self.assertIn("Subestação".encode("utf-8"), out)
        # And the result actually is what it now says it is: parsed back with
        # nothing told about the encoding, the accented name survives.
        self.assertEqual("Subestação", ET.fromstring(out)[0].get("id"))

    def test_an_unknown_encoding_name_is_treated_as_not_utf8(self):
        """`codecs.lookup` decides, and the safe direction is to correct it.

        Expat will not parse a file in an encoding it does not know, so this
        is exercised on the helper directly rather than through a document.
        """
        self.assertEqual(b'<?xml version="1.0" encoding="utf-8"?>',
                         doc_mod._reconcile_declaration(
                             b'<?xml version="1.0" encoding="x-made-up"?>'))

    def test_utf8_spelled_any_way_python_knows_is_left_alone(self):
        for spelling in (b"utf-8", b"UTF-8", b"utf8", b"UTF_8", b"U8"):
            declaration = b'<?xml version="1.0" encoding="%s"?>' % spelling
            self.assertEqual(declaration,
                             doc_mod._reconcile_declaration(declaration))

    def test_a_byte_order_mark_comes_back(self):
        """A BOM is not content and never reaches the tree, so it is recorded.

        No corpus file has one. Windows tools write them, and a file that
        lost its BOM on every save would be a diff on line 1 for ever.
        """
        source = (b'\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?>\n'
                  b'<SCL xmlns="%s">\n  %s\n</SCL>\n' % (SCL.encode(), HEADER))
        self.assertEqual(source, self.out(source))


class TestTheLayout(_Base):
    """Line endings, the final newline, and the root's attribute order."""

    def test_a_crlf_file_comes_back_crlf(self):
        source = (b'<?xml version="1.0" encoding="UTF-8"?>\r\n'
                  b'<SCL xmlns="%s">\r\n  %s\r\n</SCL>\r\n'
                  % (SCL.encode(), HEADER))
        out = self.out(source)
        self.assertEqual(source, out)
        self.assertEqual(4, out.count(b"\r\n"))

    def test_an_lf_file_does_not_gain_carriage_returns(self):
        source = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
                  b'<SCL xmlns="%s">\n  %s\n</SCL>\n' % (SCL.encode(), HEADER))
        out = self.out(source)
        self.assertEqual(source, out)
        self.assertNotIn(b"\r", out)

    def test_a_file_without_a_final_newline_does_not_gain_one(self):
        """`ElementTree` writes none of its own, so only a file that HAS one
        can lose it -- and only a file that has none can be given one."""
        source = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
                  b'<SCL xmlns="%s">\n  %s\n</SCL>' % (SCL.encode(), HEADER))
        out = self.out(source)
        self.assertEqual(source, out)
        self.assertFalse(out.endswith(b"\n"))

    def test_a_crlf_file_keeps_its_final_crlf(self):
        source = (b'<SCL xmlns="%s">\r\n  %s\r\n</SCL>\r\n'
                  % (SCL.encode(), HEADER))
        self.assertEqual(source, self.out(source))

    def test_the_roots_attribute_order_is_the_files_own(self):
        """The one element `ElementTree` reorders, because it is the one it
        adds attributes to.

        Left alone it writes every declaration it generates first, sorted by
        prefix, then the ordinary attributes; the unused declarations this
        library re-emits land after everything. This root interleaves all
        three kinds, and `xmlns:sel` is used by nothing.
        """
        root = (b'<SCL version="2007" xmlns="%s" revision="B" '
                b'xmlns:sel="%s" release="4">' % (SCL.encode(), SEL.encode()))
        source = b'<?xml version="1.0" encoding="UTF-8"?>\n%s\n  %s\n</SCL>\n' % (
            root, HEADER)
        out = self.out(source)
        self.assertEqual(source, out)
        self.assertIn(root, out)

    def test_an_attribute_added_after_parse_lands_last(self):
        """What an edit produces. Appending is the only honest answer: the
        file never said where it would have put a name it does not have."""
        source = (b'<SCL version="2007" xmlns="%s" revision="B">\n  %s\n</SCL>\n'
                  % (SCL.encode(), HEADER))
        doc = SclDocument.parse(self.file(source))
        doc.root.set("release", "4")
        self.assertIn(b'<SCL version="2007" xmlns="%s" revision="B" release="4">'
                      % SCL.encode(), doc.to_bytes())


class TestADocumentThatWasNeverAFile(_Base):
    """No layout to reproduce, so `ElementTree`'s own habits stand."""

    def test_a_hand_made_root_serialises_without_a_prolog(self):
        doc = SclDocument(ET.Element("SCL"))
        self.assertEqual(b"<SCL />", doc.to_bytes())

    def test_a_hand_made_root_can_be_written(self):
        doc = SclDocument(ET.Element("SCL"))
        dst = self.dir / "made.scd"
        doc.write(dst)
        self.assertEqual(b"<SCL />", dst.read_bytes())


class TestWrite(_Base):
    """`write` puts `to_bytes` on disk, or leaves the disk alone."""

    SOURCE = (b'<?xml version="1.0" encoding="UTF-8"?>\r\n'
              b'<SCL xmlns="%s">\r\n  <Header id="H" toolID="t" />\r\n</SCL>\r\n'
              % SCL.encode())

    def doc(self):
        return SclDocument.parse(self.file(self.SOURCE))

    def test_what_is_written_is_what_to_bytes_returns(self):
        doc = self.doc()
        dst = self.dir / "out.scd"
        doc.write(dst)
        self.assertEqual(doc.to_bytes(), dst.read_bytes())
        self.assertEqual(self.SOURCE, dst.read_bytes())

    def test_an_existing_file_is_replaced(self):
        dst = self.dir / "out.scd"
        dst.write_bytes(b"older content that is longer than what replaces it")
        self.doc().write(dst)
        self.assertEqual(self.SOURCE, dst.read_bytes())

    def test_nothing_is_left_beside_the_destination(self):
        """The temporary is built in the destination's own directory, so a
        leak would show up next to the file the user asked for."""
        doc = self.doc()
        before = self.listing()
        doc.write(self.dir / "out.scd")
        self.assertEqual(sorted(before + ["out.scd"]), self.listing())

    def test_a_failed_write_leaves_the_destination_as_it_was(self):
        """The reason the temporary exists. `cfbwrite` writes an RDB the same
        way: a half-written file that goes back into a vendor tool is worse
        than no file at all."""
        dst = self.dir / "out.scd"
        dst.write_bytes(b"the SCD that was already there")
        doc = self.doc()
        with mock.patch.object(doc_mod.os, "replace",
                               side_effect=OSError("disk went away")):
            with self.assertRaises(OSError):
                doc.write(dst)
        self.assertEqual(b"the SCD that was already there", dst.read_bytes())

    def test_a_failed_write_drops_no_temporary(self):
        dst = self.dir / "out.scd"
        dst.write_bytes(b"the SCD that was already there")
        doc = self.doc()
        before = self.listing()
        with mock.patch.object(doc_mod.os, "replace",
                               side_effect=OSError("disk went away")):
            with self.assertRaises(OSError):
                doc.write(dst)
        self.assertEqual(before, self.listing())

    def test_a_failed_write_creates_nothing_where_there_was_nothing(self):
        dst = self.dir / "out.scd"
        doc = self.doc()
        before = self.listing()
        with mock.patch.object(doc_mod.os, "replace",
                               side_effect=OSError("disk went away")):
            with self.assertRaises(OSError):
                doc.write(dst)
        self.assertEqual(before, self.listing())
        self.assertFalse(dst.exists())

    def test_a_destination_whose_directory_is_missing_raises(self):
        with self.assertRaises(OSError):
            self.doc().write(self.dir / "nope" / "out.scd")

    @unittest.skipIf(sys.platform == "win32", "POSIX permission bits")
    def test_the_written_file_is_an_ordinary_file(self):
        """`mkstemp` opens at 0600, which would leave an SCD nobody else
        could read -- including the vendor tool it is going back to."""
        dst = self.dir / "out.scd"
        self.doc().write(dst)
        self.assertEqual(0o644, stat.S_IMODE(os.stat(dst).st_mode))


if __name__ == "__main__":
    unittest.main()

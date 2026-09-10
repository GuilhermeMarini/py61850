# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Comparing a serialised SCL document against the bytes it was parsed from.

The measurement half of the round-trip test. `test_scl_roundtrip.py` holds
what must be true; this holds how the two byte strings are told apart.

**It never names a difference it cannot categorise.** "Files differ at offset
41" is not actionable when the file is 23 MB; "29 comments lost, 2 namespace
declarations dropped, CRLF replaced by LF on 660,549 lines" is. So every
comparison below answers a specific question, and the byte compare is the last
of them rather than the only one.

**It reads bytes, not the model.** Nothing here calls `iter_local`,
`_declared_namespaces` or any other reader in the library -- a harness that
asks the library under test what is in the file cannot catch the library
losing it. The namespace declarations, the comments and the prefixes are all
recovered from the raw bytes with a scanner that owes the library nothing.
That the scanner duplicates a little of what `document.py` already does is
the price of it being independent, and it is worth paying here.

## The permitted cosmetic differences

Four, and no more. Everything else is a failure.

1. **Attribute quote style** -- `a='1'` and `a="1"` are the same attribute.
   `ElementTree` always writes double quotes; a vendor may write either.
2. **Empty-element spacing** -- `<X/>` and `<X />` are the same element.
3. **CDATA sections** -- `<![CDATA[a<b]]>` and `a&lt;b` are the same text
   content. `ElementTree` cannot preserve the boundary (expat reports the
   content as ordinary character data), and no consumer can tell the
   difference through an XML parser. Seven of them are in `sel.scd`, holding
   SEL settings text.
4. **Numeric character references** -- `&#xA;` and `&#10;` are the same
   character. `ElementTree`'s attribute escaper writes the decimal form and
   the spelling is not configurable.

:func:`normalise` erases exactly those four and counts each substitution it
makes, so the report can say how much of the compare was bought by an
exception rather than earned. **It normalises nothing else.** In particular
it does not touch line endings: those are in scope for the fidelity
guarantee, not a cosmetic difference, and the byte compare is meant to fail
on them until the writer re-emits them.

The one place line endings ARE normalised is inside
:attr:`Report.lost_comments`, which matches comment text across the two
sides. That is not an exception to the above -- the byte compare and the
line-ending case both still fail on the same CRLF -- it is what keeps the
comment case answering only whether a comment is still there.
"""

import io
import re
from pathlib import Path

from py61850.scl import SclDocument

CORPUS = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "scl"

# Every markup construct that is not element content, in the order a scanner
# must try them: a comment, a CDATA section and a processing instruction can
# all contain a bare `>`, so `<[^>]*>` is only safe once those three have had
# their chance at the same position. Alternation is leftmost-first, which is
# what makes trying them in this order sufficient.
_REGION = re.compile(rb"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<[^>]*>", re.S)

# `xmlns="uri"` and `xmlns:pfx="uri"`, with the prefix CAPTURED. The library's
# own `_declared_namespaces()` collects the URI and drops the prefix, which is
# most of why A3 has work to do; recovering the pair here is also what keeps
# this harness independent of it.
_NS_DECL = re.compile(rb"""xmlns(?::([A-Za-z0-9_.\-]+))?\s*=\s*["']([^"']+)["']""")

# A prefix `ElementTree` invented because nothing registered the real one.
_INVENTED = re.compile(rb"[<\s/]((?:ns|xmlns:ns)\d+):")

# An attribute written with single quotes, inside a tag. The value may not
# contain a double quote, because rewriting that one would change its meaning.
_SQ_ATTR = re.compile(rb"""([A-Za-z_:][\w:.\-]*)\s*=\s*'([^'"]*)'""")

_TRAILING_SPACE_EMPTY = re.compile(rb"\s+/>$")

_HEX_REF = re.compile(rb"&#[xX]([0-9A-Fa-f]+);")

# XML's own end-of-line normalisation: `\r\n` and a lone `\r` both become
# `\n`. See `_eol_normalised`.
_EOL = re.compile(rb"\r\n|\r")


def _eol_normalised(text):
    """``text`` with XML's mandatory end-of-line normalisation applied.

    Used ONLY by :attr:`Report.lost_comments`, and for one reason: every
    comment in the corpus spans more than one line. SEL's "This section is
    overwritten by Architect" banner wraps across three, and all 32 comments
    in the three vendor files carry a CRLF inside their own text.

    End-of-line normalisation is mandatory in XML and happens inside expat,
    so those breaks are LF before a tree exists -- exactly as the file's own
    660,549 line endings are. Comparing comment text raw therefore reported
    all 32 as LOST on the same line that said "29 in, 29 out", which is both
    self-contradictory and unactionable: no work on the parser can change it.

    **Nothing is forgiven by normalising here.** The CRLF is still counted
    twice, by the `line_endings` case and by the byte compare, and neither
    passes until the writer re-emits the ending it read. This only stops the
    `comments` case from answering a question that is not its own, so it can
    answer the one that is: is every comment still in the file.
    """
    return _EOL.sub(b"\n", text)


def _escape_text(data):
    """`&`, `<` and `>` escaped, as ``ElementTree`` escapes character data.

    Used to bring a CDATA section's content into the form the serialiser
    writes it in, so the two can be compared at all.
    """
    return data.replace(b"&", b"&amp;").replace(b"<", b"&lt;").replace(b">", b"&gt;")


def normalise(data):
    """``(bytes, counts)`` with the four permitted cosmetic differences erased.

    ``counts`` names how many substitutions each exception bought, so a report
    can distinguish "identical" from "identical once four exceptions were
    applied 660 times".
    """
    counts = {"attr_quotes": 0, "empty_spacing": 0, "cdata_flattened": 0, "hex_refs": 0}
    out = []
    pos = 0
    for match in _REGION.finditer(data):
        out.append(data[pos:match.start()])
        piece = match.group()
        if piece.startswith(b"<![CDATA["):
            counts["cdata_flattened"] += 1
            piece = _escape_text(piece[9:-3])
        elif not piece.startswith(b"<!--") and not piece.startswith(b"<?"):
            piece, n = _SQ_ATTR.subn(rb'\1="\2"', piece)
            counts["attr_quotes"] += n
            if piece.endswith(b"/>"):
                piece, n = _TRAILING_SPACE_EMPTY.subn(b"/>", piece)
                counts["empty_spacing"] += n
        out.append(piece)
        pos = match.end()
    out.append(data[pos:])
    joined = b"".join(out)
    # Document-wide, and last: a character reference lives in element content
    # as readily as in an attribute value, and content is not a region above.
    joined, counts["hex_refs"] = _HEX_REF.subn(
        lambda m: b"&#%d;" % int(m.group(1), 16), joined)
    return joined, counts


class Facts:
    """What one side of the comparison contains, measured from its bytes."""

    __slots__ = ("size", "declaration", "comments", "declared_namespaces",
                 "invented_prefixes", "crlf", "lf", "cdata", "hex_refs",
                 "final_newline")

    def __init__(self, data):
        self.size = len(data)
        self.declaration = None
        self.comments = []
        self.declared_namespaces = []
        self.cdata = 0
        root_seen = False
        for match in _REGION.finditer(data):
            piece = match.group()
            if piece.startswith(b"<!--"):
                self.comments.append(piece[4:-3])
            elif piece.startswith(b"<![CDATA["):
                self.cdata += 1
            elif piece.startswith(b"<?"):
                if self.declaration is None and piece[:5].lower() == b"<?xml":
                    self.declaration = piece
            elif not root_seen and not piece.startswith(b"</"):
                # The first start tag is the root, and the only element whose
                # declarations the fidelity guarantee is about: SCL puts them
                # all there, and a declaration deeper in the document belongs
                # to a subtree that moves with it.
                root_seen = True
                self.declared_namespaces = [
                    (pfx or b"", uri) for pfx, uri in _NS_DECL.findall(piece)]
        self.invented_prefixes = sorted(set(_INVENTED.findall(data)))
        self.crlf = data.count(b"\r\n")
        self.lf = data.count(b"\n") - self.crlf
        # Whether the last line is terminated. The three vendor exports end at
        # `</SCL>` with nothing after it and `ElementTree` writes no trailing
        # newline either, so only a file that HAS one can lose it -- which is
        # why a hand-written fixture is the one that catches this.
        self.final_newline = data.endswith(b"\n")
        self.hex_refs = len(_HEX_REF.findall(data))

    @property
    def namespace_uris(self):
        """Every URI declared on the root, prefix ignored."""
        return {uri for _, uri in self.declared_namespaces}

    @property
    def prefix_map(self):
        """``{prefix: uri}`` for the root's declarations, ``b""`` being the default."""
        return dict(self.declared_namespaces)

    @property
    def dominant_eol(self):
        """The line ending the file is mostly written with, or ``None`` for one line."""
        if not self.crlf and not self.lf:
            return None
        return b"\r\n" if self.crlf >= self.lf else b"\n"


class Report:
    """Both sides measured, plus the byte comparison between them."""

    __slots__ = ("name", "original", "produced", "before", "after",
                 "cosmetic", "equal", "first_difference", "differing_lines")

    def __init__(self, name, original, produced):
        self.name = name
        self.original = original
        self.produced = produced
        self.before = Facts(original)
        self.after = Facts(produced)
        left, counts = normalise(original)
        right, right_counts = normalise(produced)
        self.cosmetic = {k: counts[k] + right_counts[k] for k in counts}
        self.equal = left == right
        self.first_difference = None
        self.differing_lines = 0
        if not self.equal:
            self.first_difference = _first_difference(left, right)
            self.differing_lines = _differing_lines(left, right)

    @property
    def lost_comments(self):
        """The comments that were in the input and are not in the output.

        Matched on text with line endings normalised on both sides --
        :func:`_eol_normalised` says why that is the comment case's business
        and not a softening of the guarantee.
        """
        remaining = [_eol_normalised(c) for c in self.after.comments]
        lost = []
        for comment in (_eol_normalised(c) for c in self.before.comments):
            if comment in remaining:
                remaining.remove(comment)
            else:
                lost.append(comment)
        return lost

    @property
    def dropped_namespaces(self):
        """``[(prefix, uri)]`` declared on the input root and gone from the output."""
        present = self.after.namespace_uris
        return [(p, u) for p, u in self.before.declared_namespaces if u not in present]


def _first_difference(left, right):
    """``(offset, left context, right context)`` at the first differing byte."""
    limit = min(len(left), len(right))
    offset = limit
    for i in range(limit):
        if left[i] != right[i]:
            offset = i
            break
    start = max(0, offset - 40)
    return offset, left[start:offset + 40], right[start:offset + 40]


def _differing_lines(left, right):
    """How many lines differ, counting a length mismatch as differing lines."""
    a = left.split(b"\n")
    b = right.split(b"\n")
    return sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))


def round_trip(name):
    """``(original bytes, produced bytes)`` for one corpus fixture.

    The produced side goes through `SclDocument` deliberately, rather than
    through `ElementTree` here: it is the library's own path that carries the
    fidelity guarantee, and a harness that serialised the tree itself would
    keep passing while the library's path rotted.
    """
    path = CORPUS / name
    original = path.read_bytes()
    return original, SclDocument.parse(path)._to_bytes()


def _describe(count, singular, plural=None):
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def format_report(report):
    """The gap list for one fixture, as text, whether it passed or not.

    Printed on every run rather than only on failure: `unittest` shows nothing
    for an expected failure, and the whole content of this phase is that the
    list is visible.
    """
    before, after = report.before, report.after
    lines = [f"round trip: {report.name}  {before.size:,} B in, {after.size:,} B out"]

    def row(ok, label, detail):
        lines.append(f"  [{'ok' if ok else 'GAP'}] {label:24} {detail}")

    lost = report.lost_comments
    row(not lost, "comments",
        f"{len(before.comments)} in, {len(after.comments)} out"
        + (f" -- {_describe(len(lost), 'comment')} lost" if lost else ""))

    dropped = report.dropped_namespaces
    row(not dropped, "namespace declarations",
        f"{len(before.declared_namespaces)} on the root, {len(after.declared_namespaces)} out"
        + (" -- dropped " + ", ".join(
            (p.decode() or "(default)") for p, _ in dropped) if dropped else ""))

    prefixes_ok = (before.prefix_map == after.prefix_map
                   and not after.invented_prefixes)
    row(prefixes_ok, "namespace prefixes",
        ", ".join(sorted((p.decode() or "(default)") for p in before.prefix_map))
        + (f" -- {_describe(len(after.invented_prefixes), 'prefix', 'prefixes')} invented: "
           + ", ".join(p.decode() for p in after.invented_prefixes[:4])
           if after.invented_prefixes else ""))

    row(before.dominant_eol == after.dominant_eol
        and before.final_newline == after.final_newline, "line endings",
        f"{before.crlf:,} CRLF / {before.lf:,} LF in, "
        f"{after.crlf:,} CRLF / {after.lf:,} LF out"
        + ("" if before.final_newline == after.final_newline
           else " -- final newline "
                + ("lost" if before.final_newline else "added")))

    row(before.declaration == after.declaration, "xml declaration",
        f"{(before.declaration or b'(none)').decode()} -> "
        f"{(after.declaration or b'(none)').decode()}")

    if report.equal:
        row(True, "bytes", "identical once the cosmetic exceptions were applied")
    else:
        offset, left, right = report.first_difference
        row(False, "bytes",
            f"{report.differing_lines:,} lines differ, first at byte {offset:,}")
        lines.append(f"        in : {left!r}")
        lines.append(f"        out: {right!r}")

    lines.append("        permitted and applied: "
                 + ", ".join(f"{k}={v}" for k, v in sorted(report.cosmetic.items())))
    return "\n".join(lines)

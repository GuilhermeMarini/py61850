"""MMS file services (IEC 61850 file_transfer class).

    GetServerDirectory{FILE}  -> FileDirectory
    GetFile                   -> FileOpen -> FileRead* -> FileClose

FileDirectory is one flat listing per call, so "which folders hold a .cfg?" is
built here rather than in the protocol: :meth:`walk_files` descends the tree
one FileDirectory at a time and :meth:`search_files` filters what it yields by
glob, extension and regex.
"""

import fnmatch
import os
import re

from ...errors import MmsError
from .. import pdu

SEPARATORS = "/\\"


# ---- name helpers (pure; no I/O) -----------------------------------------
def basename(name):
    """Last path component of an MMS file name ('/EVENTS/C4.CFG' -> 'C4.CFG').
    Splits on both separators: relays use '/' or '\\' depending on vendor."""
    for i in range(len(name) - 1, -1, -1):
        if name[i] in SEPARATORS:
            return name[i + 1:]
    return name


def folder_of(name):
    """Directory part of an MMS file name, with its trailing separator kept
    ('/EVENTS/C4.CFG' -> '/EVENTS/'). Names with no separator are at '/'."""
    for i in range(len(name) - 1, -1, -1):
        if name[i] in SEPARATORS:
            return name[:i + 1]
    return "/"


def _join(parent, name):
    """Make a child entry's name absolute. Most relays already report the full
    path in every listing; a few report names relative to the listed folder."""
    if not parent or not name or name[0] in SEPARATORS or name.startswith(parent):
        return name
    sep = "\\" if "\\" in parent and "/" not in parent else "/"
    return parent.rstrip(SEPARATORS) + sep + name


def looks_like_directory(entry):
    """A FileDirectory entry naming a folder rather than a file. MMS has no
    'is a directory' attribute; the trailing separator is the marker relays
    that report folders at all actually use."""
    return bool(entry.name) and entry.name[-1] in SEPARATORS


def compile_file_filter(pattern=None, *, regex=None, extensions=None,
                        ignore_case=True):
    """Build ``predicate(file_name) -> bool`` from the three filter kinds.

    ``pattern``     glob, or a list of globs; matched against the file name
                    alone unless the glob itself contains a separator, in which
                    case it is matched against the whole path.
    ``extensions``  ``['.cfg', 'dat']`` -- a leading dot is optional.
    ``regex``       a regex (str or compiled), searched in the whole path.

    Values inside one kind are OR-ed, the kinds are AND-ed: ``extensions=['cfg']``
    with ``regex='EVENTS'`` finds .cfg files under an EVENTS folder. No filter
    at all matches everything.
    """
    def fold(s):
        return s.lower() if ignore_case else s

    tests = []

    if pattern:
        globs = [pattern] if isinstance(pattern, str) else list(pattern)
        globs = [fold(g) for g in globs if g]
        if globs:
            def match_glob(name, globs=globs):
                whole, base = fold(name), fold(basename(name))
                return any(fnmatch.fnmatchcase(whole if any(c in g for c in SEPARATORS)
                                               else base, g)
                           for g in globs)
            tests.append(match_glob)

    if extensions:
        exts = [extensions] if isinstance(extensions, str) else list(extensions)
        exts = [fold(e if e.startswith(".") else "." + e) for e in exts if e]
        if exts:
            def match_ext(name, exts=exts):
                base = fold(basename(name))
                return any(base.endswith(e) for e in exts)
            tests.append(match_ext)

    if regex:
        rx = regex if hasattr(regex, "search") else re.compile(
            regex, re.IGNORECASE if ignore_case else 0)
        tests.append(lambda name, rx=rx: rx.search(name) is not None)

    if not tests:
        return lambda name: True
    return lambda name: all(t(name) for t in tests)


class FileServicesMixin:
    # ---- FileDirectory ---------------------------------------------------
    def file_directory(self, path=None):
        """List files on the server. `path` may be a directory/prefix or None."""
        entries, cont = [], None
        while True:
            resp = self._transact(pdu.build_file_directory(path, cont))
            batch, more = pdu.decode_file_directory(resp)
            entries.extend(batch)
            if not more or not batch:
                break
            cont = batch[-1].name
        return entries

    # ---- Walk / search ---------------------------------------------------
    def _list_folder(self, path):
        """One folder listing, tolerant of how the server spells a folder: some
        answer '/EVENTS' only when asked for '/EVENTS/'."""
        if path is None:
            return self.file_directory(None)
        try:
            return self.file_directory(path)
        except MmsError:
            if path[-1] in SEPARATORS:
                raise
            return self.file_directory(path + "/")

    def walk_files(self, root=None, max_depth=None, on_error=None):
        """Yield a :class:`DirEntry` for every file at or under `root`.

        The whole tree by default: every folder is descended into, however deep.
        Pass `max_depth` to stop at N levels below `root` (0 = `root` only).

        Relays answer FileDirectory in one of two ways and this handles both:
        most (SEL among them) return every file in the IED, full path and all,
        from a single root listing; others list one folder at a time and expect
        you to descend. Folders are recognised by their trailing separator, and
        each is listed once -- a folder reached twice, or deeper than
        `max_depth`, is not followed.

        The root listing's errors propagate. A folder that cannot be listed is
        skipped instead, and reported to ``on_error(path, exc)`` if given, so
        one locked subfolder does not abort a scan of the whole IED.
        """
        pending = [(root, 0)]
        seen_folders, seen_files = set(), set()
        while pending:
            path, depth = pending.pop(0)
            try:
                entries = self._list_folder(path)
            except MmsError as ex:
                if path is root:
                    raise
                if on_error is not None:
                    on_error(path, ex)
                continue
            for e in entries:
                e.name = _join(path, e.name)
                if looks_like_directory(e):
                    key = e.name.rstrip(SEPARATORS)
                    deeper = max_depth is None or depth < max_depth
                    if deeper and key and key not in seen_folders:
                        seen_folders.add(key)
                        pending.append((e.name, depth + 1))
                elif e.name not in seen_files:
                    seen_files.add(e.name)
                    yield e

    def search_files(self, pattern=None, *, regex=None, extensions=None,
                     root=None, recursive=True, ignore_case=True, max_depth=None,
                     on_error=None):
        """Find files on the IED by glob / extension / regex.

        Searches every file in every folder unless you narrow it: `root` starts
        somewhere other than the top, `max_depth` caps how deep the walk goes,
        `recursive=False` lists one folder only. With no filter at all it
        returns the IED's whole file tree.

        Returns the matching :class:`DirEntry` objects sorted by name; each
        ``.name`` is the full path, so the folder a hit lives in is
        ``folder_of(entry.name)``.

            c.search_files(extensions=[".cfg", ".dat", ".hdr"])
            c.search_files("C4_*.TXT", root="/EVENTS/")
            c.search_files(regex=r"\\d{4}-\\d{2}-\\d{2}")

        See :func:`compile_file_filter` for how the three filters combine and
        :meth:`walk_files` for how folders are discovered.
        """
        matches = compile_file_filter(pattern, regex=regex, extensions=extensions,
                                      ignore_case=ignore_case)
        if recursive:
            found = self.walk_files(root, max_depth=max_depth, on_error=on_error)
        else:
            found = self._list_folder(root)
        return sorted((e for e in found if matches(e.name)), key=lambda e: e.name)

    # ---- FileOpen / FileRead / FileClose ---------------------------------
    def file_open(self, name, initial_position=0):
        resp = self._transact(pdu.build_file_open(name, initial_position))
        return pdu.decode_file_open(resp)

    def file_read(self, frsm_id):
        """One FileRead. Returns (data_chunk, more_follows)."""
        return pdu.decode_file_read(self._transact(pdu.build_file_read(frsm_id)))

    def file_close(self, frsm_id):
        self._transact(pdu.build_file_close(frsm_id))

    # ---- GetFile (high level) --------------------------------------------
    def get_file(self, name, progress=None):
        """Download a file fully into memory. Returns the bytes.

        `progress(bytes_so_far, reported_size)` is called once at 0 (so a bar
        can learn the size FileOpen reports) and after every chunk.
        """
        frsm_id, size = self.file_open(name)
        chunks = []
        got = 0
        if progress:
            progress(0, size)
        try:
            while True:
                data, more = self.file_read(frsm_id)
                chunks.append(data)
                got += len(data)
                if progress:
                    progress(got, size)
                if not more:
                    break
        finally:
            try:
                self.file_close(frsm_id)
            except Exception:
                pass
        return b"".join(chunks)

    def download_file(self, name, local_path, progress=None):
        """Download a file straight to disk. Returns bytes written."""
        data = self.get_file(name, progress=progress)
        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(data)
        return len(data)

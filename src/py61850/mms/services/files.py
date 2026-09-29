# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""MMS file services (IEC 61850 file_transfer class).

    GetServerDirectory{FILE}  -> FileDirectory
    GetFile                   -> FileOpen -> FileRead* -> FileClose

GetFile retries and resumes when asked to; how, and how a resume is verified,
is in :mod:`.transfer`.

FileDirectory is one flat listing per call, so "which folders hold a .cfg?" is
built here rather than in the protocol: :meth:`walk_files` descends the tree
one FileDirectory at a time and :meth:`search_files` filters what it yields by
glob, extension and regex.
"""

import fnmatch
import re
import time
from dataclasses import replace

from ...errors import Iec61850Error, MmsError, TransportError
from .. import pdu
from .transfer import (
    MemorySink,
    PartFileSink,
    TransferState,
    is_position_invalid,
    load_part,
)

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
    #: Set once this server has refused, or silently ignored, an
    #: initialPosition: resuming against it only ever restarts, so stop trying.
    _seek_refused = False

    def file_open(self, name, initial_position=0):
        """FileOpen. Returns ``(frsm_id, reported_size)``."""
        frsm_id, size, _ = self._file_open(name, initial_position)
        return frsm_id, size

    def _file_open(self, name, initial_position=0):
        """FileOpen -> ``(frsm_id, size, last_modified)``."""
        resp = self._transact(pdu.build_file_open(name, initial_position))
        return pdu.decode_file_open(resp)

    def file_read(self, frsm_id):
        """One FileRead. Returns (data_chunk, more_follows)."""
        return pdu.decode_file_read(self._transact(pdu.build_file_read(frsm_id)))

    def file_close(self, frsm_id):
        self._transact(pdu.build_file_close(frsm_id))

    # ---- GetFile (high level) --------------------------------------------
    def get_file(self, name, progress=None, *, retry=None, resume_from=None,
                 on_retry=None):
        """Download a file fully into memory. Returns the bytes.

        `progress(bytes_so_far, reported_size)` is called once when the file is
        opened (so a bar can learn the size FileOpen reports) and after every
        chunk. A resumed or restarted transfer calls it again from where it
        now stands.

        A FileClose that fails is never silent: the IED may still hold the
        handle, and once its few handles are gone every FileOpen answers
        file-busy. After a failed read the read error propagates with the close
        failure noted on it; after a good read the close failure raises.

        Retry and resume, at whichever level the caller wants them:

        * ``retry=RetryPolicy(...)`` -- the client retries on its own: backs
          off on file-busy, reconnects after a transport failure, resumes from
          the bytes it has. ``on_retry(attempt, error, offset)`` is called
          before each retry. Without a policy nothing is retried.
        * On its own terms -- every :class:`~py61850.Iec61850Error` raised here
          carries ``.transfer``, a :class:`~py61850.TransferState`; reconnect
          when you choose and pass it back as ``resume_from``.
          :func:`~py61850.is_retryable` says whether retrying can help.

        A resume is verified before it is trusted (see
        :mod:`py61850.mms.services.transfer`); one that fails verification
        restarts from byte 0.
        """
        state = TransferState(name)
        sink = MemorySink()
        if resume_from is not None:
            if resume_from.name != name:
                raise MmsError(f"resume_from is a transfer of {resume_from.name!r}, "
                               f"not of {name!r}")
            if resume_from.data is None:
                raise MmsError("resume_from holds no data -- a download_file "
                               "transfer resumes with download_file(resume=True)")
            state = replace(resume_from, data=None, path=None)
            sink = MemorySink(resume_from.data)
            state.offset = sink.size
        self._get(sink, state, progress, retry, on_retry)
        return bytes(sink.buf)

    def download_file(self, name, local_path, progress=None, *, retry=None,
                      resume=False, on_retry=None):
        """Download a file straight to disk. Returns bytes written.

        Bytes go to ``local_path + ".part"`` as they arrive and the file is
        renamed into place only once complete, so ``local_path`` never holds a
        partial file. A failed download leaves the ``.part``, and a
        ``.part.json`` sidecar naming the remote file, behind; a later call
        with ``resume=True`` continues it after the same checks an in-process
        resume makes. Without ``resume`` an old ``.part`` is overwritten.

        ``retry``, ``on_retry`` and the ``.transfer`` an error carries work as
        for :meth:`get_file`; the state's ``path`` is the ``.part`` file.
        """
        origin = {"host": self.host, "port": self.port, "name": name}
        state = load_part(local_path, origin) if resume else None
        sink = PartFileSink(local_path, origin, keep=state is not None)
        if state is None:
            state = TransferState(name)
        state.offset = sink.size
        try:
            self._get(sink, state, progress, retry, on_retry)
        except BaseException:
            sink.close()
            raise
        sink.commit()
        return state.offset

    # ---- the transfer loop -------------------------------------------------
    def _get(self, sink, state, progress, retry, on_retry):
        """Run one GetFile into `sink`, retrying as `retry` allows."""
        attempt = 0
        # A client whose association died on an earlier file starts on a new one.
        reconnect = retry is not None and retry.reconnect and not self.associated
        while True:
            try:
                if reconnect:
                    self.close()
                    self.connect()
                    reconnect = False
                self._get_once(sink, state, progress)
                return
            except Iec61850Error as ex:
                state.offset = sink.size
                sink.fill(state)
                ex.transfer = replace(state)           # type: ignore[attr-defined]
                state.data = None
                if retry is None or attempt >= retry.retries or not retry.allows(ex):
                    raise
                attempt += 1
                if on_retry is not None:
                    on_retry(attempt, ex, state.offset)
                time.sleep(retry.delay_before(attempt))
                reconnect = isinstance(ex, TransportError)
                if not retry.resume:
                    sink.reset()
                    state.forget()

    def _open_identified(self, name, initial_position=0):
        """FileOpen for GetFile, with a reported size of 0 read as unknown.

        Schneider MiCOM (P139, P632) and GE UR (L90) answer every FileOpen with
        sizeOfFile 0 and no lastModified, then send tens of kilobytes. Taken at
        its word, that 0 would fail the length check on every file. A truly
        empty file loses nothing by it: there is nothing to check.
        """
        frsm_id, size, lm = self._file_open(name, initial_position)
        return frsm_id, size or None, lm

    def _get_once(self, sink, state, progress):
        """FileOpen -> FileRead* -> FileClose, continuing what `sink` holds
        when that can be verified, from byte 0 when it cannot."""
        frsm_id, overlap = None, 0
        if sink.size and state.identified and not self._seek_refused:
            state.offset = sink.size
            overlap = state.overlap()
            try:
                frsm_id, size, lm = self._open_identified(state.name,
                                                          sink.size - overlap)
            except MmsError as ex:
                if not is_position_invalid(ex):
                    raise
                self._seek_refused = True
            else:
                if not state.same_file(size, lm):
                    self.file_close(frsm_id)           # not the file we have part of
                    frsm_id = None
        if frsm_id is None:
            sink.reset()
            state.forget()
            overlap = 0
            frsm_id, size, lm = self._open_identified(state.name)
            state.size, state.last_modified = size, lm
            sink.identify(state)
        if progress:
            progress(sink.size, size)
        # The first `overlap` bytes read must equal the tail already held.
        pending = b"" if overlap else None
        try:
            while True:
                data, more = self.file_read(frsm_id)
                state.chunk = max(state.chunk, len(data))
                if pending is not None:
                    pending += data
                    if len(pending) < overlap and more:
                        continue
                    if pending[:overlap] != sink.tail(overlap):
                        raise _OverlapMismatch
                    data, pending = pending[overlap:], None
                sink.append(data)
                state.offset = sink.size
                if progress:
                    progress(sink.size, size)
                if not more:
                    break
        except _OverlapMismatch:
            # Changed content, or a server that ignored initialPosition: either
            # way these bytes cannot be continued, and seeking here is suspect.
            self.file_close(frsm_id)
            self._seek_refused = True
            sink.reset()
            state.forget()
            self._get_once(sink, state, progress)
            return
        except BaseException as ex:
            try:
                self.file_close(frsm_id)
            except Iec61850Error as close_ex:
                ex.add_note(f"FileClose of FRSM {frsm_id} also failed: {close_ex}")
            raise
        try:
            self.file_close(frsm_id)
        except Iec61850Error as ex:
            raise MmsError(f"{state.name} was read but FileClose of FRSM {frsm_id} "
                           f"failed, so the IED may still hold it open: {ex}") from ex
        # sizeOfFile is a hint, not a promise: SEL relays overstate the files
        # they generate (CFG.TXT: 2632 reported, 2267 sent, the same on every
        # read), so a single pass ends where the server says it ends. A pass
        # that continued earlier bytes is held to it: a length that disagrees
        # there means the splice is suspect, and the file is read once more.
        if overlap and size is not None and sink.size != size:
            self._seek_refused = True
            sink.reset()
            state.forget()
            self._get_once(sink, state, progress)


class _OverlapMismatch(Exception):
    """The re-read overlap differs from the bytes already held."""

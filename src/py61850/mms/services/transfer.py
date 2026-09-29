# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Retry and resume for GetFile (FileOpen -> FileRead* -> FileClose).

Who decides *whether* to retry is the caller's choice, made at one of three
levels; *how* a resumed transfer is checked is always this module's:

    caller owns it     catch the error, read ``ex.transfer`` (a
                       :class:`TransferState`), reconnect however it likes and
                       pass the state back as ``get_file(resume_from=...)``
    library owns it    ``get_file(retry=RetryPolicy(...))`` -- the client
                       classifies, backs off, reconnects and resumes itself
    command line       ``mms-files --retries N --continue``

MMS resumes natively -- FileOpen carries an initialPosition -- but it has no
checksum service, so a resume is trusted only after three checks:

    identity   FileOpen reports the same sizeOfFile as the first open, or --
               when the server reports no size -- the same lastModified
               (catches a rotated event file, a growing COMTRADE)
    overlap    the reopen starts ``overlap`` bytes early and those bytes must
               equal the tail already held (catches changed content, and a
               server that ignores initialPosition and sends from byte 0)
    length     a resumed transfer ends at exactly sizeOfFile bytes (a
               transfer read in one pass ends where the server says: SEL
               relays overstate the size of the files they generate)

Any check that fails restarts the file from byte 0; it is never an error.
"""

import json
import os
from dataclasses import dataclass

from ...errors import MmsError, TransportError

#: The fewest bytes a resume re-reads to prove it is continuing the same file.
MIN_OVERLAP = 512

#: Sidecar format written next to a ``.part`` file; bump when the keys change.
SIDECAR_VERSION = 1

PART_SUFFIX = ".part"
SIDECAR_SUFFIX = ".part.json"


@dataclass
class TransferState:
    """Where an interrupted GetFile stopped. Plain data, tied to no client: it
    can be handed to another :class:`~py61850.FileTransfer`, or saved and
    reloaded, and passed back as ``get_file(..., resume_from=state)``.

    ``offset`` bytes are held -- in ``data`` for :meth:`get_file`, in the
    ``path`` (``.part``) file for :meth:`download_file`. ``size`` and
    ``last_modified`` are what the first FileOpen reported.
    """

    name: str
    size: int | None = None
    last_modified: str | None = None
    offset: int = 0
    chunk: int = 0                  # largest FileRead seen, to size the overlap
    data: bytes | None = None
    path: str | None = None

    @property
    def identified(self):
        """Whether a FileOpen has reported this file's identity yet."""
        return self.size is not None or self.last_modified is not None

    def same_file(self, size, last_modified):
        """Whether a FileOpen reporting ``size`` / ``last_modified`` is the
        file this state holds part of.

        The size decides whenever it is known. lastModified is a weaker
        witness than it looks: Siemens SIPROTEC 5 reports the relay's own
        clock on every FileOpen, so it differs between two opens of an
        untouched file seconds apart, and would refuse every resume on a
        server that seeks correctly. It decides only when there is no size.
        The overlap check runs either way.
        """
        if self.size is not None:
            return size == self.size
        return last_modified == self.last_modified

    def overlap(self):
        """Bytes a resume re-reads before the offset: one chunk, at least
        :data:`MIN_OVERLAP`, never more than is held."""
        return min(self.offset, max(MIN_OVERLAP, self.chunk))

    def forget(self):
        """Drop the identity and the offset: the next open starts over."""
        self.size = self.last_modified = None
        self.offset = self.chunk = 0


@dataclass
class RetryPolicy:
    """How :meth:`~py61850.FileTransfer.get_file` retries on its own.

    ``retries``   attempts after the first; 0 keeps the fail-fast behaviour
    ``delay``     seconds before the first retry, multiplied by ``backoff``
                  for each one after, capped at ``max_delay``
    ``reconnect`` after a transport failure, drop the connection and
                  associate again -- without it transport errors are final
    ``resume``    continue from the bytes already held (verified); off
                  restarts every attempt from byte 0

    A reconnect is a **new association**: anything tied to the old one is
    gone. For files that is harmless.
    """

    retries: int = 2
    delay: float = 1.0
    backoff: float = 3.0
    max_delay: float = 30.0
    reconnect: bool = True
    resume: bool = True

    def delay_before(self, attempt):
        """Seconds to wait before retry number ``attempt`` (1-based)."""
        return min(self.delay * self.backoff ** (attempt - 1), self.max_delay)

    def allows(self, exc):
        if isinstance(exc, TransportError):
            return self.reconnect
        return is_retryable(exc)


def is_retryable(exc):
    """Whether retrying could change the answer ``exc`` gave.

    A transport failure (timeout, reset, refused connection) is retryable, but
    only on a new association -- the old one is gone. ``file-busy`` is too: the
    server's few file handles free up, often once an orphaned one from an
    earlier failure is released. Everything else -- a missing file, access
    denied, a reject -- is final, and retrying it would only hide the fault.
    """
    if isinstance(exc, TransportError):
        return True
    if isinstance(exc, MmsError):
        return (exc.error_class, exc.error_code) == ("file", "file-busy")
    return False


def is_position_invalid(exc):
    return isinstance(exc, MmsError) and \
        (exc.error_class, exc.error_code) == ("file", "position-invalid")


# ---- where the bytes go ----------------------------------------------------
class MemorySink:
    """The bytes of a :meth:`get_file` call."""

    def __init__(self, data=b""):
        self.buf = bytearray(data)

    @property
    def size(self):
        return len(self.buf)

    def append(self, data):
        self.buf += data

    def tail(self, n):
        return bytes(self.buf[len(self.buf) - n:]) if n else b""

    def reset(self):
        self.buf.clear()

    def identify(self, state):
        pass

    def fill(self, state):
        """Record on ``state`` where its bytes are, for an error to carry."""
        state.data, state.path = bytes(self.buf), None


class PartFileSink:
    """The ``.part`` file of a :meth:`download_file` call, and the sidecar
    that says which remote file it is part of.

    Chunks are flushed as they arrive, so a process that dies leaves on disk
    every byte it had -- and the overlap check, not the sidecar, is what
    decides whether those bytes are trusted.
    """

    def __init__(self, local_path, origin, keep):
        self.local_path = local_path
        self.part = local_path + PART_SUFFIX
        self.sidecar = local_path + SIDECAR_SUFFIX
        self.origin = origin            # {"host", "port", "name"}
        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
        self.f = open(self.part, "r+b" if keep and os.path.exists(self.part) else "w+b")
        self.f.seek(0, os.SEEK_END)

    @property
    def size(self):
        return self.f.tell()

    def append(self, data):
        self.f.write(data)
        self.f.flush()

    def tail(self, n):
        if not n:
            return b""
        end = self.f.tell()
        self.f.seek(end - n)
        data = self.f.read(n)
        self.f.seek(end)
        return data

    def reset(self):
        self.f.seek(0)
        self.f.truncate()

    def identify(self, state):
        """Write the sidecar once FileOpen has said what the file is."""
        meta = dict(self.origin, format=SIDECAR_VERSION, size=state.size,
                    last_modified=state.last_modified)
        tmp = self.sidecar + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        os.replace(tmp, self.sidecar)

    def fill(self, state):
        state.data, state.path = None, self.part

    def commit(self):
        """Move the finished file under its real name."""
        self.f.close()
        os.replace(self.part, self.local_path)
        try:
            os.remove(self.sidecar)
        except FileNotFoundError:
            pass

    def close(self):
        """Stop after a failure. A ``.part`` with nothing in it is removed,
        with its sidecar: there is nothing to resume."""
        empty = self.size == 0
        self.f.close()
        if empty:
            for path in (self.part, self.sidecar):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass


def load_part(local_path, origin):
    """The :class:`TransferState` a previous run left next to ``local_path``,
    or ``None`` if there is nothing to resume.

    The sidecar has to match exactly -- format, host, port and remote name --
    or the ``.part`` is not ours to continue. It is not merged or repaired:
    anything unexpected means start over.
    """
    part, sidecar = local_path + PART_SUFFIX, local_path + SIDECAR_SUFFIX
    try:
        with open(sidecar, encoding="utf-8") as f:
            meta = json.load(f)
        offset = os.path.getsize(part)
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("format") != SIDECAR_VERSION:
        return None
    if any(meta.get(k) != v for k, v in origin.items()):
        return None
    size, lm = meta.get("size"), meta.get("last_modified")
    if not (size is None or isinstance(size, int)) or \
            not (lm is None or isinstance(lm, str)):
        return None
    return TransferState(origin["name"], size=size, last_modified=lm,
                         offset=offset, path=part)

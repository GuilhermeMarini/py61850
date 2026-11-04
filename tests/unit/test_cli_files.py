# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""What `mms-files` tells the person waiting for a download."""

import contextlib
import io
import os
import re
import tempfile
import unittest
from collections import deque
from unittest import mock

from py61850 import DirEntry, FileTransfer
from py61850.cli import files as cli
from py61850.core import ber
from py61850.mms import pdu
from py61850.osi import stack

BODY = bytes(range(256)) * 12              # 3072 bytes
CHUNK = 600


class FakeRelay:
    """Stands in for CotpTransport and serves BODY, CHUNK bytes to a FileRead."""

    def __init__(self, sends=BODY):
        self.sends = sends                  # FileOpen always reports len(BODY)
        self.answers = deque()
        self.pos = 0

    def connect(self):
        pass

    def close(self):
        pass

    def send(self, data):
        mms = stack.extract_mms_from_response(data)
        if mms[0] == pdu.CONCLUDE_REQUEST:
            self.answers.append(ber.tlv(pdu.CONCLUDE_RESPONSE, b""))
        elif mms[0] == pdu.CONFIRMED_REQUEST:
            self.answers.append(self._confirmed(mms))
        else:
            self.answers.append(ber.tlv(pdu.INITIATE_RESPONSE,
                                        ber.int_tlv(0x80, 12000)))

    def recv(self):
        return stack.wrap_mms_pdu(self.answers.popleft())

    def _confirmed(self, mms):
        _, body, _ = ber.read_tlv(mms, 0)
        (_, invoke), (service, _) = list(ber.iter_tlv(body))
        if service == pdu.SVC_FILE_OPEN:
            self.pos = 0
            reply = ber.tlv(0x80, ber.enc_int(1)) + ber.tlv(
                0xA1, ber.tlv(0x80, ber.enc_uint(len(BODY))))
        elif service == pdu.SVC_FILE_READ:
            data = self.sends[self.pos:self.pos + CHUNK]
            self.pos += len(data)
            reply = ber.tlv(0x80, data) + ber.tlv(0x81, b"\xff" if data else b"\x00")
        else:
            reply = b""
        return pdu.confirmed_response(ber.to_int(invoke), ber.tlv(service, reply))


class TestDuration(unittest.TestCase):
    def test_a_quick_file_keeps_its_tenths(self):
        self.assertEqual(cli._htime(0.42), "0.4s")
        self.assertEqual(cli._htime(59.9), "59.9s")

    def test_minutes_and_seconds(self):
        self.assertEqual(cli._htime(377), "6m17s")     # the slow-link .DAT, piped
        self.assertEqual(cli._htime(964), "16m04s")    # and one at a time

    def test_hours(self):
        self.assertEqual(cli._htime(3725), "1h02m05s")

    def test_rounding_up_to_a_minute_does_not_print_sixty(self):
        self.assertEqual(cli._htime(59.97), "1m00s")
        self.assertEqual(cli._htime(119.6), "2m00s")


class TestProgressBar(unittest.TestCase):
    def bar(self, total=1000):
        self.clock, stream = [100.0], io.StringIO()
        patcher = mock.patch.object(cli.time, "monotonic", lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        return cli.ProgressBar(total, label="C4.DAT", stream=stream), stream

    def test_shows_elapsed_and_what_is_left_while_running(self):
        bar, stream = self.bar()
        self.clock[0] += 377
        bar.update(500)
        self.assertIn("elapsed 6m17s  eta 6m17s", stream.getvalue())

    def test_a_finished_bar_says_how_long_it_took_and_nothing_is_left(self):
        bar, stream = self.bar()
        self.clock[0] += 377
        bar.finish()
        last = stream.getvalue().splitlines()[-1]
        self.assertIn("elapsed 6m17s", last)
        self.assertNotIn("eta", last)

    def test_a_failed_bar_still_says_how_long_it_ran(self):
        bar, stream = self.bar()
        self.clock[0] += 12
        bar.finish(complete=False)
        self.assertIn("elapsed 12.0s", stream.getvalue())

    def test_an_unknown_size_still_shows_elapsed(self):
        """Schneider and GE report 0 for every file, so there is no ETA to give
        and the elapsed time is all the person waiting has."""
        bar, stream = self.bar(total=0)
        self.clock[0] += 90
        bar.update(500)
        self.assertIn("elapsed 1m30s", stream.getvalue())
        self.assertNotIn("eta", stream.getvalue())

    def test_elapsed_counts_from_the_start_of_the_file(self):
        bar, _ = self.bar()
        self.clock[0] += 5
        bar.update(10)
        self.clock[0] += 5
        self.assertEqual(bar.elapsed, 10)


class TestDownloadCommands(unittest.TestCase):
    DURATION = r"[\d.hms]+"
    sends = BODY

    def client(self, *args, **kwargs):
        c = FileTransfer("192.0.2.1")
        c.t = FakeRelay(self.sends)
        return c

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(cli, "FileTransfer", self.client), \
                mock.patch.object(cli, "_select", lambda *a, **k: [
                    DirEntry("/E/A.DAT", len(BODY), None),
                    DirEntry("/E/B.DAT", len(BODY), None)]), \
                contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(out):
            code = cli.main(["192.0.2.1", *argv, "--out", tmp])
            self.assertFalse(code)
            self.err = err.getvalue()
            return out.getvalue(), os.path.basename(tmp)

    def test_get_says_how_long_the_file_took(self):
        out, _ = self.run_cli("--get", "/E/A.DAT")
        self.assertRegex(out, rf"saved -> .*\(3,072 bytes in {self.DURATION}\)")

    def test_get_all_keeps_a_running_total(self):
        out, _ = self.run_cli("--get-all")
        lines = re.findall(rf"overall .*elapsed {self.DURATION}", out)
        self.assertEqual(len(lines), 2)

    def test_get_all_ends_with_the_total(self):
        out, tmp = self.run_cli("--get-all")
        self.assertRegex(out, rf"done: 2/2 files, .* -> .*{tmp}/ in {self.DURATION}")

    def test_get_saves_a_short_file_and_says_so(self):
        self.sends = BODY[:1000]
        out, _ = self.run_cli("--get", "/E/A.DAT")
        self.assertIn("saved -> ", out)
        self.assertIn("(1,000 bytes in ", out)
        self.assertIn("[short] /E/A.DAT: 1,000 of the 3,072 bytes the relay reported",
                      self.err)
        self.assertIn(cli.SHORT_NOTE, self.err)

    def test_a_complete_file_gets_no_warning(self):
        self.run_cli("--get", "/E/A.DAT")
        self.assertNotIn("[short]", self.err)
        self.assertNotIn(cli.SHORT_NOTE, self.err)

    def test_view_says_so_too(self):
        self.sends = BODY[:1000]
        self.run_cli("--view", "/E/A.DAT")
        self.assertIn("[short] /E/A.DAT", self.err)

    def test_get_all_names_each_short_file_and_counts_them(self):
        self.sends = BODY[:1000]
        self.run_cli("--get-all")
        self.assertEqual(self.err.count("[short]"), 2)
        self.assertEqual(self.err.count(cli.SHORT_NOTE), 1)
        self.assertIn("2 files ended short of the size the relay reported.", self.err)


if __name__ == "__main__":
    unittest.main()

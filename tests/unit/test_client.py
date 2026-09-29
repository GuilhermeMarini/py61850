# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The client and its service mixins, driven against a fake transport.

No socket is opened. This is only possible because ``MmsClientBase`` does one
thing -- move bytes between :mod:`py61850.osi.stack` and
:mod:`py61850.mms.pdu` -- so replacing its transport replaces the network.
"""

import os
import tempfile
import unittest

from py61850 import (
    FileTransfer,
    MmsClient,
    MmsError,
    RetryPolicy,
    TransferState,
    TransportError,
    folder_of,
    is_retryable,
)
from py61850.core import ber
from py61850.core import data as d
from py61850.mms import pdu
from py61850.osi import stack


class FakeTransport:
    """Stands in for CotpTransport. Queued responses are MMS PDUs; a queued
    exception is raised instead of answering."""

    def __init__(self, responses):
        self.queued = list(responses)
        self.sent = []
        self.connected = False
        self.closed = False

    def connect(self):
        self.connected = True

    def send(self, data):
        self.sent.append(data)

    def recv(self):
        if not self.queued:
            raise AssertionError("client sent more requests than the test queued")
        item = self.queued.pop(0)
        if isinstance(item, Exception):
            raise item
        return stack.wrap_mms_pdu(item)

    def close(self):
        self.closed = True

    # ---- inspection helpers ---------------------------------------------
    def requests(self):
        """The service TLV of each confirmed request the client sent."""
        out = []
        for raw in self.sent:
            mms = stack.extract_mms_from_response(raw)
            if mms[0] != pdu.CONFIRMED_REQUEST:
                continue
            _, body, _ = ber.read_tlv(mms, 0)
            for t, v in ber.iter_tlv(body):
                if t != 0x02:
                    out.append(ber.tlv(t, v))
        return out


def make_client(cls, responses, associate=True):
    client = cls("192.0.2.1")
    queue = list(responses)
    if associate:
        queue.insert(0, ber.tlv(pdu.INITIATE_RESPONSE, b""))
    client.t = FakeTransport(queue)
    if associate:
        client.connect()
    return client


def response(service_bytes, invoke=1):
    return pdu.confirmed_response(invoke, service_bytes)


def name_list(names, more=False):
    body = ber.tlv(0xA0, b"".join(ber.tlv(0x1A, n.encode()) for n in names))
    body += ber.tlv(0x81, b"\xff" if more else b"\x00")
    return response(ber.tlv(pdu.SVC_GET_NAME_LIST, body))


class TestAssociation(unittest.TestCase):
    def test_connect_sends_initiate_and_accepts_response(self):
        c = make_client(MmsClient, [])
        self.assertTrue(c.t.connected)
        sent = stack.extract_mms_from_response(c.t.sent[0])
        self.assertEqual(sent, pdu.build_initiate())

    def test_refused_association_raises(self):
        c = MmsClient("192.0.2.1")
        c.t = FakeTransport([ber.tlv(pdu.REJECT, b"")])  # reject instead of initiate-Response
        with self.assertRaises(MmsError):
            c.connect()

    def test_context_manager_closes(self):
        c = MmsClient("192.0.2.1")
        c.t = FakeTransport([ber.tlv(pdu.INITIATE_RESPONSE, b""),
                             ber.tlv(pdu.CONCLUDE_RESPONSE, b"")])
        with c:
            pass
        self.assertTrue(c.t.closed)

    def test_close_concludes_the_association_first(self):
        """Without a Conclude the server keeps the association -- and any file
        it had open -- until its own timer runs out."""
        c = make_client(MmsClient, [ber.tlv(pdu.CONCLUDE_RESPONSE, b"")])
        c.close()
        self.assertEqual(stack.extract_mms_from_response(c.t.sent[-1]),
                         pdu.build_conclude())
        self.assertTrue(c.t.closed)
        self.assertFalse(c.associated)

    def test_close_still_closes_when_the_conclude_goes_unanswered(self):
        c = make_client(MmsClient, [TransportError("timed out")])
        c.close()
        self.assertTrue(c.t.closed)

    def test_close_skips_the_conclude_after_the_transport_failed(self):
        c = make_client(MmsClient, [TransportError("connection closed by peer")])
        with self.assertRaises(TransportError):
            c.get_server_directory()
        sent = len(c.t.sent)
        c.close()
        self.assertEqual(len(c.t.sent), sent)          # nothing more written
        self.assertTrue(c.t.closed)

    def test_close_without_an_association_sends_nothing(self):
        c = make_client(MmsClient, [], associate=False)
        c.close()
        self.assertEqual(c.t.sent, [])
        self.assertTrue(c.t.closed)

    def test_connect_keeps_the_negotiated_limits(self):
        """The Initiate-Response carries the ceilings a batching read needs;
        nothing else in the client can discover them."""
        body = (ber.tlv(0x80, b"\x2e\xe0")           # localDetailCalled = 12000
                + ber.int_tlv(0x81, 3) + ber.int_tlv(0x82, 3))
        c = MmsClient("192.0.2.1")
        c.t = FakeTransport([ber.tlv(pdu.INITIATE_RESPONSE, body)])
        c.connect()
        self.assertEqual(c.max_pdu_size, 12000)
        self.assertEqual(c.max_outstanding, 3)

    def test_silent_initiate_response_leaves_conservative_defaults(self):
        c = make_client(MmsClient, [])                 # associates with an empty PDU
        self.assertEqual(c.max_pdu_size, MmsClient.DEFAULT_MAX_PDU_SIZE)
        self.assertEqual(c.max_outstanding, 1)

    def test_invoke_id_increments_per_transaction(self):
        c = make_client(MmsClient, [name_list(["LD0"]), name_list(["A"])])
        c.get_server_directory()
        c.get_logical_device_directory("LD0")
        self.assertEqual(c.invoke, 2)

    def test_invoke_id_wraps(self):
        c = make_client(MmsClient, [name_list(["LD0"])])
        c.invoke = 0x7FFF
        c.get_server_directory()
        self.assertEqual(c.invoke, 0)


class TestDirectoryServices(unittest.TestCase):
    def test_get_server_directory(self):
        c = make_client(MmsClient, [name_list(["LD0", "LD1"])])
        self.assertEqual(c.get_server_directory(), ["LD0", "LD1"])
        self.assertEqual(c.t.requests(),
                         [pdu.build_get_name_list(pdu.CLASS_DOMAIN, "vmd")])

    def test_follows_more_follows(self):
        c = make_client(MmsClient, [name_list(["A", "B"], more=True),
                                    name_list(["C"], more=False)])
        self.assertEqual(c.get_logical_device_directory("LD0"), ["A", "B", "C"])
        second = c.t.requests()[1]
        self.assertEqual(second, pdu.build_get_name_list(
            pdu.CLASS_NAMED_VARIABLE, "domain", "LD0", "B"))

    def test_data_set_directory_uses_named_variable_list_class(self):
        c = make_client(MmsClient, [name_list(["DS1"])])
        c.get_data_set_directory("LD0")
        self.assertEqual(c.t.requests()[0], pdu.build_get_name_list(
            pdu.CLASS_NAMED_VARIABLE_LIST, "domain", "LD0"))


class TestLogicalNodes(unittest.TestCase):
    """The LN list is one GetNameList reduced to its first components -- MMS
    has no service that returns logical nodes on their own."""

    VARS = ["LLN0", "LLN0$ST$Beh$stVal", "G1PTOC1", "G1PTOC1$ST$Op$general",
            "G2PTOC1", "MET3PMMXU1", "MET3PMMXU1$MX$A$phsA$cVal$mag$f"]

    def test_get_logical_nodes_reduces_the_variable_list(self):
        c = make_client(MmsClient, [name_list(self.VARS)])
        nodes = c.get_logical_nodes("LD0")
        self.assertEqual([ln.name for ln in nodes],
                         ["LLN0", "G1PTOC1", "G2PTOC1", "MET3PMMXU1"])
        self.assertEqual(len(c.t.requests()), 1)      # no extra round trips
        self.assertEqual(nodes[1].ref, "LD0/G1PTOC1")

    def test_logical_nodes_are_found_when_the_server_lists_only_leaves(self):
        c = make_client(MmsClient, [name_list(["G1PTOC1$ST$Op$general",
                                               "MET3PMMXU1$MX$A"])])
        self.assertEqual([ln.name for ln in c.get_logical_nodes("LD0")],
                         ["G1PTOC1", "MET3PMMXU1"])

    def test_get_logical_nodes_filters_by_class(self):
        c = make_client(MmsClient, [name_list(self.VARS)])
        nodes = c.get_logical_nodes("LD0", ln_class=["ptoc"])   # case-insensitive
        self.assertEqual([ln.name for ln in nodes], ["G1PTOC1", "G2PTOC1"])

    def test_get_logical_nodes_filters_by_glob_and_regex(self):
        c = make_client(MmsClient, [name_list(self.VARS)])
        self.assertEqual([ln.name for ln in c.get_logical_nodes("LD0", pattern="G?PTOC*")],
                         ["G1PTOC1", "G2PTOC1"])
        c = make_client(MmsClient, [name_list(self.VARS)])
        self.assertEqual([ln.name for ln in c.get_logical_nodes("LD0", regex=r"^LD0/MET")],
                         ["MET3PMMXU1"])

    def test_find_logical_nodes_sweeps_every_logical_device(self):
        c = make_client(MmsClient, [name_list(["LD0", "LD1"]),
                                    name_list(["G1PTOC1", "LLN0"]),
                                    name_list(["MET3PMMXU1", "LLN0"])])
        hits = c.find_logical_nodes("MMXU")
        self.assertEqual([ln.ref for ln in hits], ["LD1/MET3PMMXU1"])
        self.assertEqual(len(c.t.requests()), 3)      # server dir + one per LD

    def test_find_logical_nodes_can_be_limited_to_one_device(self):
        c = make_client(MmsClient, [name_list(["G1PTOC1"])])
        self.assertEqual([ln.ref for ln in c.find_logical_nodes(ld="LD0")],
                         ["LD0/G1PTOC1"])
        self.assertEqual(len(c.t.requests()), 1)      # no GetServerDirectory


def _refs_of(read_request):
    """The (domainId, itemId) pairs a read [4] request names, in wire order."""
    _, spec, _ = ber.read_tlv(read_request, 0)          # unwrap 0xA4
    _, access, _ = ber.read_tlv(spec, 0)                # varAccessSpec [1]
    _, entries, _ = ber.read_tlv(access, 0)             # listOfVariable [0]
    out = []
    for _, entry in ber.iter_tlv(entries):              # SEQUENCE per variable
        _, name, _ = ber.read_tlv(entry, 0)             # variableSpecification [0]
        _, obj, _ = ber.read_tlv(name, 0)               # domain-specific [1]
        dom, item = [v for _, v in ber.iter_tlv(obj)][-2:]
        out.append((dom.decode(), item.decode()))
    return out


def _names_of(read_request):
    """The itemIds a read [4] request names, in wire order."""
    return [item for _, item in _refs_of(read_request)]


class TestReadServices(unittest.TestCase):
    def test_read_value(self):
        svc = ber.tlv(pdu.SVC_READ, ber.tlv(0xA1, d.encode_boolean(True)))
        c = make_client(MmsClient, [response(svc)])
        self.assertIs(c.read_value("LD0", "LLN0$ST$Beh$stVal"), True)
        self.assertEqual(c.t.requests()[0],
                         pdu.build_read("LD0", "LLN0$ST$Beh$stVal"))

    def test_read_value_of_empty_response(self):
        c = make_client(MmsClient, [response(ber.tlv(pdu.SVC_READ, b""))])
        self.assertIsNone(c.read_value("LD0", "X"))

    def test_read_many_is_one_request(self):
        svc = ber.tlv(pdu.SVC_READ, ber.tlv(
            0xA1, d.encode_boolean(True) + d.encode_integer(7)))
        c = make_client(MmsClient, [response(svc)])
        self.assertEqual(c.read_many("LD0", ["A$ST$X$stVal", "A$ST$Y$stVal"]),
                         [True, 7])
        self.assertEqual(len(c.t.requests()), 1)
        self.assertEqual(c.t.requests()[0],
                         pdu.build_read_multi("LD0", ["A$ST$X$stVal",
                                                      "A$ST$Y$stVal"]))

    def test_read_many_splits_at_the_negotiated_pdu_size(self):
        """Each batch must fit one MMS PDU; overshooting is not an error the
        relay reports, it drops the association."""
        items = [f"ACN1GGIO1$ST$Ind{i}$stVal" for i in range(40)]
        one = ber.tlv(pdu.SVC_READ, ber.tlv(0xA1, d.encode_boolean(True)))
        c = make_client(MmsClient, [response(one)] * 8)
        c.max_pdu_size = 512                            # ~ 6 names per request
        c.read_many("LD0", items)
        sent = c.t.requests()
        self.assertGreater(len(sent), 1)
        self.assertLessEqual(max(len(r) for r in sent), 512)
        # every name asked for exactly once, in order
        names = [it for r in sent for it in _names_of(r)]
        self.assertEqual(names, items)

    def test_read_many_of_nothing_sends_nothing(self):
        c = make_client(MmsClient, [])
        self.assertEqual(c.read_many("LD0", []), [])
        self.assertEqual(c.t.requests(), [])

    def test_read_refs_names_several_devices_in_one_request(self):
        svc = ber.tlv(pdu.SVC_READ, ber.tlv(
            0xA1, d.encode_boolean(True) + d.encode_integer(7)))
        c = make_client(MmsClient, [response(svc)])
        refs = [("LD0", "A$ST$X$stVal"), ("LD1", "LLN0$ST$Beh$stVal")]
        self.assertEqual(c.read_refs(refs), [True, 7])
        self.assertEqual(len(c.t.requests()), 1)
        self.assertEqual(c.t.requests()[0], pdu.build_read_refs(refs))
        self.assertEqual(_refs_of(c.t.requests()[0]), refs)

    def test_read_refs_splits_at_the_negotiated_pdu_size(self):
        refs = [(f"LD{i % 3}", f"ACN1GGIO1$ST$Ind{i}$stVal") for i in range(40)]
        one = ber.tlv(pdu.SVC_READ, ber.tlv(0xA1, d.encode_boolean(True)))
        c = make_client(MmsClient, [response(one)] * 8)
        c.max_pdu_size = 512
        c.read_refs(refs)
        sent = c.t.requests()
        self.assertGreater(len(sent), 1)
        self.assertLessEqual(max(len(r) for r in sent), 512)
        # every pair asked for exactly once, in the caller's order
        got = [pair for r in sent for pair in _refs_of(r)]
        self.assertEqual(got, refs)

    def test_read_refs_of_nothing_sends_nothing(self):
        c = make_client(MmsClient, [])
        self.assertEqual(c.read_refs([]), [])
        self.assertEqual(c.t.requests(), [])

    def test_read_data_set(self):
        svc = ber.tlv(pdu.SVC_READ, ber.tlv(
            0xA1, d.encode_boolean(False) + d.encode_integer(2)))
        c = make_client(MmsClient, [response(svc)])
        self.assertEqual(c.read_data_set("LD0", "BRDSet01"), [False, 2])
        self.assertEqual(c.t.requests()[0],
                         pdu.build_read_named_list("LD0", "BRDSet01"))

    def test_service_error_surfaces_as_mms_error(self):
        err = ber.tlv(pdu.CONFIRMED_ERROR,
                      ber.tlv(0x80, b"\x01")
                      + ber.tlv(0xA2, ber.tlv(0xA0, ber.tlv(0x87, b"\x03"))))
        c = make_client(MmsClient, [err])
        with self.assertRaises(MmsError):
            c.read_value("LD0", "X")


class TestFileServices(unittest.TestCase):
    def _dir_response(self, names, more=False):
        entries = b""
        for n in names:
            attrs = ber.tlv(0x80, ber.enc_uint(10)) + ber.tlv(0x81, b"20240101120000Z")
            entries += ber.tlv(0x30, ber.tlv(0xA0, ber.tlv(0x19, n.encode()))
                               + ber.tlv(0xA1, attrs))
        body = ber.tlv(0xA0, entries) + ber.tlv(0x81, b"\xff" if more else b"\x00")
        return response(ber.tlv(pdu.SVC_FILE_DIRECTORY, body))

    def _read_response(self, chunk, more):
        body = ber.tlv(0x80, chunk) + ber.tlv(0x81, b"\xff" if more else b"\x00")
        return response(ber.tlv(pdu.SVC_FILE_READ, body))

    def _open_response(self, frsm=1, size=6, modified=None):
        attrs = ber.tlv(0x80, ber.enc_uint(size))
        if modified:
            attrs += ber.tlv(0x81, modified.encode())
        body = ber.tlv(0x80, ber.enc_int(frsm)) + ber.tlv(0xA1, attrs)
        return response(ber.tlv(pdu.SVC_FILE_OPEN, body))

    def test_composition_has_both_service_groups(self):
        c = make_client(FileTransfer, [])
        self.assertTrue(hasattr(c, "get_server_directory"))   # DirectoryMixin
        self.assertTrue(hasattr(c, "read_value"))             # ReadMixin
        self.assertTrue(hasattr(c, "file_directory"))         # FileServicesMixin
        self.assertNotIn("file_directory", dir(MmsClient))

    def test_file_directory_paginates(self):
        c = make_client(FileTransfer, [self._dir_response(["/A"], more=True),
                                       self._dir_response(["/B"], more=False)])
        entries = c.file_directory()
        self.assertEqual([e.name for e in entries], ["/A", "/B"])
        self.assertEqual(c.t.requests()[1], pdu.build_file_directory(None, "/A"))

    def test_get_file_reassembles_chunks(self):
        c = make_client(FileTransfer, [
            self._open_response(frsm=4, size=6),
            self._read_response(b"abc", more=True),
            self._read_response(b"def", more=False),
            response(ber.tlv(pdu.SVC_FILE_CLOSE, b"")),
        ])
        self.assertEqual(c.get_file("/X.TXT"), b"abcdef")
        self.assertEqual(c.t.requests()[3], pdu.build_file_close(4))

    def test_progress_callback_reports_size_first(self):
        c = make_client(FileTransfer, [
            self._open_response(frsm=1, size=6),
            self._read_response(b"abcdef", more=False),
            response(ber.tlv(pdu.SVC_FILE_CLOSE, b"")),
        ])
        seen = []
        c.get_file("/X.TXT", progress=lambda got, total: seen.append((got, total)))
        self.assertEqual(seen, [(0, 6), (6, 6)])

    def test_file_is_closed_even_when_a_read_fails(self):
        c = make_client(FileTransfer, [
            self._open_response(frsm=9),
            MmsError("hardware-fault"),
            response(ber.tlv(pdu.SVC_FILE_CLOSE, b"")),
        ])
        with self.assertRaises(MmsError):
            c.get_file("/X.TXT")
        self.assertEqual(c.t.requests()[-1], pdu.build_file_close(9))

    def test_get_file_with_a_negative_frsm_id(self):
        c = make_client(FileTransfer, [
            self._open_response(frsm=-1538521107, size=3),
            self._read_response(b"abc", more=False),
            response(ber.tlv(pdu.SVC_FILE_CLOSE, b"")),
        ])
        self.assertEqual(c.get_file("/X.TXT"), b"abc")
        raw = bytes.fromhex("a44c07ed")
        self.assertEqual(c.t.requests()[1:], [ber.tlv(pdu.SVC_FILE_READ, raw),
                                              ber.tlv(pdu.SVC_FILE_CLOSE, raw)])

    def test_failed_close_after_a_good_read_raises(self):
        """A leaked handle is what leaves an IED answering file-busy to every
        later FileOpen, so a failed FileClose must not pass silently."""
        c = make_client(FileTransfer, [
            self._open_response(frsm=5, size=3),
            self._read_response(b"abc", more=False),
            TransportError("timed out"),
        ])
        with self.assertRaises(MmsError) as cm:
            c.get_file("/X.TXT")
        self.assertIn("FileClose of FRSM 5", str(cm.exception))
        self.assertIsInstance(cm.exception.__cause__, TransportError)

    def test_failed_close_after_a_failed_read_is_noted_on_the_read_error(self):
        c = make_client(FileTransfer, [
            self._open_response(frsm=9),
            MmsError("hardware-fault"),
            MmsError("file-busy"),
        ])
        with self.assertRaises(MmsError) as cm:
            c.get_file("/X.TXT")
        self.assertEqual(str(cm.exception), "hardware-fault")
        self.assertIn("FileClose of FRSM 9 also failed: file-busy",
                      cm.exception.__notes__)

    def test_walk_is_one_listing_when_the_relay_reports_full_paths(self):
        """The SEL answers a root FileDirectory with every file in the IED, so
        the walk must not issue a second request."""
        c = make_client(FileTransfer, [
            self._dir_response(["/CFG.TXT", "/EVENTS/C4.CFG", "/EVENTS/C4.DAT"])])
        names = [e.name for e in c.walk_files()]
        self.assertEqual(names, ["/CFG.TXT", "/EVENTS/C4.CFG", "/EVENTS/C4.DAT"])
        self.assertEqual(len(c.t.requests()), 1)

    def test_walk_descends_into_reported_folders(self):
        c = make_client(FileTransfer, [
            self._dir_response(["/CFG.TXT", "/EVENTS/"]),
            self._dir_response(["/EVENTS/C4.CFG", "/EVENTS/OLD/"]),
            self._dir_response(["/EVENTS/OLD/C1.CFG"]),
        ])
        names = [e.name for e in c.walk_files()]
        self.assertEqual(names, ["/CFG.TXT", "/EVENTS/C4.CFG", "/EVENTS/OLD/C1.CFG"])
        self.assertEqual(c.t.requests()[1], pdu.build_file_directory("/EVENTS/"))

    def test_walk_makes_relative_entries_absolute(self):
        c = make_client(FileTransfer, [
            self._dir_response(["/EVENTS/"]),
            self._dir_response(["C4.CFG"]),
        ])
        self.assertEqual([e.name for e in c.walk_files()], ["/EVENTS/C4.CFG"])

    def test_walk_descends_without_a_depth_limit_by_default(self):
        c = make_client(FileTransfer, [
            self._dir_response(["/A/"]),
            self._dir_response(["/A/B/"]),
            self._dir_response(["/A/B/C/"]),
            self._dir_response(["/A/B/C/D/"]),
            self._dir_response(["/A/B/C/D/deep.CFG"]),
        ])
        self.assertEqual([e.name for e in c.walk_files()], ["/A/B/C/D/deep.CFG"])

    def test_walk_honours_max_depth(self):
        c = make_client(FileTransfer, [
            self._dir_response(["/EVENTS/"]),
            self._dir_response(["/EVENTS/C4.CFG", "/EVENTS/OLD/"]),
        ])
        names = [e.name for e in c.walk_files(max_depth=1)]
        self.assertEqual(names, ["/EVENTS/C4.CFG"])       # /EVENTS/OLD/ not listed

    def test_walk_skips_a_folder_it_cannot_list(self):
        c = make_client(FileTransfer, [
            self._dir_response(["/A/", "/B/"]),
            MmsError("file-access-denied"),               # /A/ is locked
            self._dir_response(["/B/OK.CFG"]),
        ])
        seen = []
        names = [e.name for e in c.walk_files(on_error=lambda p, ex: seen.append(p))]
        self.assertEqual(names, ["/B/OK.CFG"])
        self.assertEqual(seen, ["/A/"])

    def test_walk_propagates_an_error_on_the_root_listing(self):
        c = make_client(FileTransfer, [MmsError("object-non-existent")])
        with self.assertRaises(MmsError):
            list(c.walk_files())

    def test_search_files_by_extension_across_folders(self):
        c = make_client(FileTransfer, [
            self._dir_response(["/CFG.TXT", "/EVENTS/C4.cfg", "/EVENTS/C4.DAT",
                                "/COMTRADE/R1.HDR", "/COMTRADE/notes.txt"])])
        hits = c.search_files(extensions=["cfg", ".dat", "hdr"])
        self.assertEqual([e.name for e in hits],
                         ["/COMTRADE/R1.HDR", "/EVENTS/C4.DAT", "/EVENTS/C4.cfg"])
        self.assertEqual({folder_of(e.name) for e in hits},
                         {"/COMTRADE/", "/EVENTS/"})

    def test_search_files_glob_matches_the_file_name_and_regex_the_path(self):
        names = ["/EVENTS/C4_10117.TXT", "/EVENTS/C4_20117.TXT", "/OLD/C4_10117.TXT"]
        c = make_client(FileTransfer, [self._dir_response(names)])
        self.assertEqual([e.name for e in c.search_files("C4_1*.TXT")],
                         ["/EVENTS/C4_10117.TXT", "/OLD/C4_10117.TXT"])
        c = make_client(FileTransfer, [self._dir_response(names)])
        self.assertEqual([e.name for e in c.search_files(regex=r"^/EVENTS/.*2011\d")],
                         ["/EVENTS/C4_20117.TXT"])

    def test_search_files_can_stay_in_one_folder(self):
        c = make_client(FileTransfer, [self._dir_response(["/EVENTS/C4.CFG"])])
        self.assertEqual(len(c.search_files(root="/EVENTS/", recursive=False)), 1)
        self.assertEqual(c.t.requests()[0], pdu.build_file_directory("/EVENTS/"))

    def test_search_files_retries_a_folder_spelling_without_the_separator(self):
        c = make_client(FileTransfer, [MmsError("file-non-existent"),
                                       self._dir_response(["/EVENTS/C4.CFG"])])
        self.assertEqual(len(c.search_files(root="/EVENTS", recursive=False)), 1)
        self.assertEqual(c.t.requests()[1], pdu.build_file_directory("/EVENTS/"))

    def test_download_file_writes_to_disk(self):
        c = make_client(FileTransfer, [
            self._open_response(frsm=1, size=3),
            self._read_response(b"abc", more=False),
            response(ber.tlv(pdu.SVC_FILE_CLOSE, b"")),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "nested", "out.txt")
            self.assertEqual(c.download_file("/X.TXT", target), 3)
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"abc")

    def test_download_file_is_renamed_into_place_only_when_complete(self):
        c = make_client(FileTransfer, [
            self._open_response(frsm=1, size=3),
            self._read_response(b"abc", more=False),
            response(ber.tlv(pdu.SVC_FILE_CLOSE, b"")),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "out.txt")
            c.download_file("/X.TXT", target)
            self.assertEqual(os.listdir(tmp), ["out.txt"])   # no .part, no sidecar

    def test_a_reported_size_of_zero_means_unknown(self):
        """Recorded from a Schneider P139: sizeOfFile 0, no lastModified, and
        then 37620 bytes. The GE L90 answers the same way."""
        opened = response(bytes.fromhex("bf4809800202b3a103800100"))
        c = make_client(FileTransfer, [opened, self._read_response(b"abc", more=True),
                                       self._read_response(b"def", more=False),
                                       response(ber.tlv(pdu.SVC_FILE_CLOSE, b""))])
        self.assertEqual(c.get_file("/COMTRADE/r0691.cfg"), b"abcdef")

    def test_a_single_pass_ends_where_the_server_says(self):
        """SEL relays overstate generated files: CFG.TXT on a 487E is reported
        as 2632 bytes and sent as 2267, identically on every read."""
        c = make_client(FileTransfer, [
            self._open_response(frsm=1, size=10),
            self._read_response(b"abc", more=True),
            self._read_response(b"", more=False),
            response(ber.tlv(pdu.SVC_FILE_CLOSE, b"")),
        ])
        self.assertEqual(c.get_file("/CFG.TXT"), b"abc")

def service_error(cls_tag, code):
    return ber.tlv(pdu.CONFIRMED_ERROR,
                   ber.tlv(0x80, b"\x01")
                   + ber.tlv(0xA2, ber.tlv(0xA0, ber.tlv(cls_tag, bytes([code])))))


FILE_BUSY = service_error(0x8B, 2)
POSITION_INVALID = service_error(0x8B, 5)
FILE_NON_EXISTENT = service_error(0x8B, 7)
INITIATE = ber.tlv(pdu.INITIATE_RESPONSE, b"")
CLOSED = response(ber.tlv(pdu.SVC_FILE_CLOSE, b""))
MODIFIED = "20260927101500Z"

# 1800 bytes the fake relay serves in three FileReads of 600.
BODY = bytes(range(256)) * 7 + bytes(8)
SIZE = len(BODY)
CHUNKS = [BODY[0:600], BODY[600:1200], BODY[1200:]]


class TestRetryAndResume(unittest.TestCase):
    """GetFile's retry and resume: who drives it varies, the checks do not."""

    _open = TestFileServices._open_response
    _read = TestFileServices._read_response

    def open_(self, frsm=1, modified=MODIFIED, size=SIZE):
        return self._open(frsm=frsm, size=size, modified=modified)

    def read(self, i):
        return self._read(CHUNKS[i], more=i < len(CHUNKS) - 1)

    @staticmethod
    def opens(c):
        """The initialPosition of every FileOpen the client sent."""
        out = []
        for req in c.t.requests():
            if req[:2] == bytes([0xBF, 0x48]):
                _, body, _ = ber.read_tlv(req, 0)
                for t, v in ber.iter_tlv(body):
                    if t == 0x81:
                        out.append(int.from_bytes(v, "big"))
        return out

    def interrupted(self):
        """A client whose read of BODY failed after two chunks, and its error."""
        c = make_client(FileTransfer, [self.open_(), self.read(0), self.read(1),
                                       TransportError("timed out"), CLOSED])
        with self.assertRaises(TransportError) as cm:
            c.get_file("/E/C4.DAT")
        return c, cm.exception

    # ---- the caller owns the policy ----------------------------------------
    def test_every_error_carries_where_the_transfer_stopped(self):
        _, ex = self.interrupted()
        state = ex.transfer
        self.assertIsInstance(state, TransferState)
        self.assertEqual((state.name, state.offset, state.size, state.last_modified),
                         ("/E/C4.DAT", 1200, len(BODY), MODIFIED))
        self.assertEqual(state.data, BODY[:1200])

    def test_resume_from_reopens_one_chunk_early_and_checks_the_overlap(self):
        _, ex = self.interrupted()
        c = make_client(FileTransfer, [self.open_(), self.read(1), self.read(2), CLOSED])
        self.assertEqual(c.get_file("/E/C4.DAT", resume_from=ex.transfer), BODY)
        self.assertEqual(self.opens(c), [600])

    def test_a_changed_file_is_downloaded_again_from_the_start(self):
        _, ex = self.interrupted()
        c = make_client(FileTransfer, [
            self.open_(size=SIZE + 10), CLOSED,               # rotated since
            self.open_(size=SIZE + 10),
            self.read(0), self.read(1), self.read(2), CLOSED])
        self.assertEqual(c.get_file("/E/C4.DAT", resume_from=ex.transfer), BODY)
        self.assertEqual(self.opens(c), [600, 0])

    def test_a_moving_last_modified_does_not_refuse_a_resume(self):
        """Siemens SIPROTEC 5 reports its own clock as lastModified on every
        FileOpen -- 191844Z, then 191848Z, for an untouched file -- while the
        size is exact. The size decides, and the overlap still has to match."""
        _, ex = self.interrupted()
        c = make_client(FileTransfer, [self.open_(modified="20260928191848Z"),
                                       self.read(1), self.read(2), CLOSED])
        self.assertEqual(c.get_file("/E/C4.DAT", resume_from=ex.transfer), BODY)
        self.assertEqual(self.opens(c), [600])

    def test_without_a_size_last_modified_decides(self):
        state = TransferState("/X", size=None, last_modified=MODIFIED)
        self.assertTrue(state.same_file(None, MODIFIED))
        self.assertFalse(state.same_file(None, "20260928191848Z"))
        self.assertFalse(TransferState("/X", size=SIZE).same_file(SIZE + 1, MODIFIED))

    def test_resume_as_recorded_from_a_siemens_7sj85(self):
        """The 7SJ85's FileOpen-Response verbatim, then the same file opened
        again later: new lastModified, same 9605 bytes."""
        first = bytes.fromhex("bf481d8004e6b83934a11580022585810f"
                              "32303236303932383139313733345a")    # ...191734Z
        later = first[:-7] + b"191848Z"
        data = bytes(range(256)) * 37 + bytes(133)                 # 9605 bytes
        c = make_client(FileTransfer, [response(first),
                                       self._read(data[:4000], more=True),
                                       self._read(data[4000:8000], more=True),
                                       TransportError("timed out"), CLOSED])
        with self.assertRaises(TransportError) as cm:
            c.get_file("/COMTRADE/FRA22387.cfg")
        c = make_client(FileTransfer, [response(later),
                                       self._read(data[4000:8000], more=True),
                                       self._read(data[8000:], more=False), CLOSED])
        self.assertEqual(c.get_file("/COMTRADE/FRA22387.cfg",
                                    resume_from=cm.exception.transfer), data)
        self.assertEqual(self.opens(c), [4000])

    def test_a_server_that_ignores_the_position_is_caught_by_the_overlap(self):
        """It answers FileOpen at 600 and then sends from byte 0 anyway. Without
        the overlap check the file would come out corrupt, silently."""
        _, ex = self.interrupted()
        c = make_client(FileTransfer, [
            self.open_(), self.read(0), CLOSED,                 # from 0, not 600
            self.open_(), self.read(0), self.read(1), self.read(2), CLOSED])
        self.assertEqual(c.get_file("/E/C4.DAT", resume_from=ex.transfer), BODY)
        self.assertEqual(self.opens(c), [600, 0])
        self.assertTrue(c._seek_refused)

    def test_after_one_failed_overlap_the_client_stops_seeking(self):
        _, ex = self.interrupted()
        c = make_client(FileTransfer, [self.open_(), self.read(0), self.read(1),
                                       self.read(2), CLOSED])
        c._seek_refused = True
        self.assertEqual(c.get_file("/E/C4.DAT", resume_from=ex.transfer), BODY)
        self.assertEqual(self.opens(c), [0])

    def test_a_resume_whose_length_disagrees_is_read_again(self):
        """The overlap matched, but the spliced total is not sizeOfFile: the
        splice cannot be trusted, so the file is read once more from 0."""
        _, ex = self.interrupted()
        c = make_client(FileTransfer, [
            self.open_(), self.read(1), self._read(CHUNKS[2][:100], more=False), CLOSED,
            self.open_(), self.read(0), self.read(1), self.read(2), CLOSED])
        self.assertEqual(c.get_file("/E/C4.DAT", resume_from=ex.transfer), BODY)
        self.assertEqual(self.opens(c), [600, 0])

    def test_position_invalid_restarts_from_the_start(self):
        _, ex = self.interrupted()
        c = make_client(FileTransfer, [POSITION_INVALID, self.open_(), self.read(0),
                                       self.read(1), self.read(2), CLOSED])
        self.assertEqual(c.get_file("/E/C4.DAT", resume_from=ex.transfer), BODY)
        self.assertEqual(self.opens(c), [600, 0])

    def test_resume_from_does_not_modify_the_callers_state(self):
        _, ex = self.interrupted()
        c = make_client(FileTransfer, [self.open_(), self.read(1), self.read(2), CLOSED])
        c.get_file("/E/C4.DAT", resume_from=ex.transfer)
        self.assertEqual(ex.transfer.offset, 1200)

    def test_resume_from_another_file_is_refused(self):
        _, ex = self.interrupted()
        c = make_client(FileTransfer, [])
        with self.assertRaises(MmsError):
            c.get_file("/E/OTHER.DAT", resume_from=ex.transfer)

    def test_classification(self):
        self.assertTrue(is_retryable(TransportError("reset")))
        self.assertTrue(is_retryable(MmsError("x", error_class="file",
                                              error_code="file-busy")))
        self.assertFalse(is_retryable(MmsError("x", error_class="file",
                                               error_code="file-non-existent")))
        self.assertFalse(is_retryable(MmsError("rejected: pdu-error/invalid-pdu")))

    # ---- the library owns the policy ---------------------------------------
    def test_no_policy_means_no_retry(self):
        c = make_client(FileTransfer, [FILE_BUSY])
        with self.assertRaises(MmsError):
            c.get_file("/E/C4.DAT")

    def test_a_transport_failure_reconnects_and_resumes(self):
        c = make_client(FileTransfer, [
            self.open_(), self.read(0), self.read(1),
            TransportError("timed out"), CLOSED,               # the close note
            INITIATE,                                          # new association
            self.open_(), self.read(1), self.read(2), CLOSED])
        seen = []
        data = c.get_file("/E/C4.DAT", retry=RetryPolicy(delay=0),
                          on_retry=lambda n, ex, off: seen.append((n, type(ex), off)))
        self.assertEqual(data, BODY)
        self.assertEqual(seen, [(1, TransportError, 1200)])
        self.assertEqual(self.opens(c), [0, 600])
        self.assertTrue(c.associated)

    def test_file_busy_is_retried_on_the_same_association(self):
        c = make_client(FileTransfer, [FILE_BUSY, FILE_BUSY, self.open_(),
                                       self.read(0), self.read(1), self.read(2), CLOSED])
        self.assertEqual(c.get_file("/E/C4.DAT", retry=RetryPolicy(delay=0)), BODY)
        self.assertEqual(len(c.t.sent), 1 + 7)       # the one association, 7 requests

    def test_a_final_error_is_not_retried(self):
        c = make_client(FileTransfer, [FILE_NON_EXISTENT])
        seen = []
        with self.assertRaises(MmsError):
            c.get_file("/E/C4.DAT", retry=RetryPolicy(delay=0),
                       on_retry=lambda *a: seen.append(a))
        self.assertEqual(seen, [])

    def test_retries_run_out(self):
        c = make_client(FileTransfer, [FILE_BUSY, FILE_BUSY, FILE_BUSY])
        with self.assertRaises(MmsError) as cm:
            c.get_file("/E/C4.DAT", retry=RetryPolicy(retries=2, delay=0))
        self.assertEqual(cm.exception.error_code, "file-busy")

    def test_without_reconnect_a_transport_failure_is_final(self):
        c = make_client(FileTransfer, [TransportError("reset")])
        with self.assertRaises(TransportError):
            c.get_file("/E/C4.DAT", retry=RetryPolicy(delay=0, reconnect=False))

    def test_without_resume_every_attempt_starts_over(self):
        c = make_client(FileTransfer, [
            self.open_(), self.read(0), TransportError("timed out"), CLOSED,
            INITIATE, self.open_(), self.read(0), self.read(1), self.read(2), CLOSED])
        data = c.get_file("/E/C4.DAT", retry=RetryPolicy(delay=0, resume=False))
        self.assertEqual(data, BODY)
        self.assertEqual(self.opens(c), [0, 0])

    def test_a_client_left_unassociated_reconnects_before_the_first_attempt(self):
        c = make_client(FileTransfer, [INITIATE, self.open_(), self.read(0),
                                       self.read(1), self.read(2), CLOSED])
        c.associated = False                     # an earlier file's transport died
        self.assertEqual(c.get_file("/E/C4.DAT", retry=RetryPolicy(delay=0)), BODY)

    def test_backoff_grows_and_is_capped(self):
        p = RetryPolicy(delay=1, backoff=3, max_delay=5)
        self.assertEqual([p.delay_before(n) for n in (1, 2, 3)], [1, 3, 5])

    # ---- across processes: the .part file ----------------------------------
    def failed_download(self, target):
        c = make_client(FileTransfer, [self.open_(), self.read(0), self.read(1),
                                       TransportError("timed out"), CLOSED])
        with self.assertRaises(TransportError) as cm:
            c.download_file("/E/C4.DAT", target)
        return cm.exception

    def test_a_failed_download_leaves_a_part_and_never_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "C4.DAT")
            ex = self.failed_download(target)
            self.assertFalse(os.path.exists(target))
            with open(target + ".part", "rb") as f:
                self.assertEqual(f.read(), BODY[:1200])
            self.assertTrue(os.path.exists(target + ".part.json"))
            self.assertEqual(ex.transfer.path, target + ".part")
            self.assertIsNone(ex.transfer.data)

    def test_a_download_refused_before_any_byte_leaves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "C4.DAT")
            c = make_client(FileTransfer, [FILE_BUSY])
            with self.assertRaises(MmsError):
                c.download_file("/E/C4.DAT", target)
            self.assertEqual(os.listdir(tmp), [])

    def test_resume_continues_a_part_left_by_an_earlier_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "C4.DAT")
            self.failed_download(target)
            # The sidecar does not know the chunk size, so the overlap is 512.
            c = make_client(FileTransfer, [self.open_(),
                                           self._read(BODY[688:1288], more=True),
                                           self._read(BODY[1288:], more=False),
                                           CLOSED])
            self.assertEqual(c.download_file("/E/C4.DAT", target, resume=True),
                             len(BODY))
            with open(target, "rb") as f:
                self.assertEqual(f.read(), BODY)
            self.assertEqual(os.listdir(tmp), ["C4.DAT"])
            self.assertEqual(self.opens(c), [1200 - 512])

    def test_without_resume_an_old_part_is_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "C4.DAT")
            self.failed_download(target)
            c = make_client(FileTransfer, [self.open_(), self.read(0), self.read(1),
                                           self.read(2), CLOSED])
            c.download_file("/E/C4.DAT", target)
            self.assertEqual(self.opens(c), [0])
            with open(target, "rb") as f:
                self.assertEqual(f.read(), BODY)

    def test_a_part_from_another_relay_is_not_resumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "C4.DAT")
            self.failed_download(target)
            c = make_client(FileTransfer, [self.open_(), self.read(0), self.read(1),
                                           self.read(2), CLOSED])
            c.host = "192.0.2.99"
            c.download_file("/E/C4.DAT", target, resume=True)
            self.assertEqual(self.opens(c), [0])

    def test_a_corrupt_sidecar_means_start_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "C4.DAT")
            self.failed_download(target)
            with open(target + ".part.json", "w") as f:
                f.write("{not json")
            c = make_client(FileTransfer, [self.open_(), self.read(0), self.read(1),
                                           self.read(2), CLOSED])
            c.download_file("/E/C4.DAT", target, resume=True)
            self.assertEqual(self.opens(c), [0])

    def test_a_download_retries_into_the_same_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "C4.DAT")
            c = make_client(FileTransfer, [
                self.open_(), self.read(0), self.read(1),
                TransportError("timed out"), CLOSED,
                INITIATE, self.open_(), self.read(1), self.read(2), CLOSED])
            n = c.download_file("/E/C4.DAT", target, retry=RetryPolicy(delay=0))
            self.assertEqual(n, len(BODY))
            with open(target, "rb") as f:
                self.assertEqual(f.read(), BODY)
            self.assertEqual(self.opens(c), [0, 600])


if __name__ == "__main__":
    unittest.main()

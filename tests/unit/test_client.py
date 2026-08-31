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

from py61850 import FileTransfer, MmsClient, MmsError, folder_of
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
        c.t = FakeTransport([ber.tlv(0xA3, b"")])       # reject instead of initiate-Response
        with self.assertRaises(MmsError):
            c.connect()

    def test_context_manager_closes(self):
        c = MmsClient("192.0.2.1")
        c.t = FakeTransport([ber.tlv(pdu.INITIATE_RESPONSE, b"")])
        with c:
            pass
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

    def _open_response(self, frsm=1, size=6):
        body = ber.tlv(0x80, ber.enc_uint(frsm))
        body += ber.tlv(0xA1, ber.tlv(0x80, ber.enc_uint(size)))
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


if __name__ == "__main__":
    unittest.main()

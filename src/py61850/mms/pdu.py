# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""MMS PDU codec -- builders and decoders, no I/O.

Everything here is a pure function over bytes.  The client calls the builders
and the decoders; an MMS server (ROADMAP 1.0) calls the same functions in the
opposite direction -- decoding the requests these builders produce, and building
the responses these decoders read.  That symmetry only survives if this module
never grows a socket, so it does not import :mod:`py61850.osi` or
:mod:`py61850.mms.client`.

    IEC 61850 ACSI service          -> underlying MMS service
    --------------------------------------------------------------------
    GetServerDirectory (LD list)    -> GetNameList(class=domain, scope=vmd)
    GetLogicalDeviceDirectory       -> GetNameList(class=namedVariable, scope=domain)
      (data sets)                   -> GetNameList(class=namedVariableList, scope=domain)
    GetDataDefinition               -> GetVariableAccessAttributes(name)
    GetDataValues / GetSGCBValues / -> Read(name)
      GetBRCBValues
    GetServerDirectory{FILE}        -> FileDirectory
    GetFile                         -> FileOpen -> FileRead* -> FileClose
"""

from ..core import ber
from ..core.data import decode_data
from ..errors import MmsError
from .service_error import decode_service_error

# ---- PDU-level CHOICE tags ----------------------------------------------
CONFIRMED_REQUEST = 0xA0
CONFIRMED_RESPONSE = 0xA1
CONFIRMED_ERROR = 0xA2
REJECT = 0xA3
INITIATE_REQUEST = 0xA8
INITIATE_RESPONSE = 0xA9

# ---- confirmed-service CHOICE tags --------------------------------------
SVC_GET_NAME_LIST = 0xA1
SVC_READ = 0xA4
SVC_WRITE = 0xA5
SVC_GET_VAR_ACCESS_ATTRIBUTES = 0xA6

# File services use the high-tag-number form. FileOpen/FileDirectory carry a
# SEQUENCE (constructed, 0xBF..); FileRead/FileClose carry a primitive
# Integer32, so their context tag must use the primitive form (0x9F..).
SVC_FILE_OPEN = 0xBF48        # [72] SEQUENCE  (constructed)
SVC_FILE_READ = 0x9F49        # [73] Integer32 (primitive)
SVC_FILE_CLOSE = 0x9F4A       # [74] Integer32 (primitive)
SVC_FILE_DIRECTORY = 0xBF4D   # [77] SEQUENCE  (constructed)

# ---- ObjectClass basicObjectClass INTEGER values ------------------------
CLASS_NAMED_VARIABLE = 0
CLASS_NAMED_VARIABLE_LIST = 2
CLASS_DOMAIN = 9

VISIBLE_STRING = 0x1A         # Identifier / VisibleString, as MMS names use it
GRAPHIC_STRING = 0x19         # FileName components


# ==== envelope ============================================================
def confirmed_request(invoke_id: int, service: bytes) -> bytes:
    return ber.tlv(CONFIRMED_REQUEST, ber.int_tlv(0x02, invoke_id) + service)


def confirmed_response(invoke_id: int, service: bytes) -> bytes:
    """The server-side mirror of :func:`confirmed_request`."""
    return ber.tlv(CONFIRMED_RESPONSE, ber.int_tlv(0x02, invoke_id) + service)


def object_name(domain: str, item: str) -> bytes:
    """ObjectName -> domain-specific [1] { domainId, itemId } (as VisibleString)."""
    ds = ber.tlv(VISIBLE_STRING, domain.encode()) + ber.tlv(VISIBLE_STRING, item.encode())
    return ber.tlv(0xA1, ds)                          # domain-specific [1] IMPLICIT SEQ


def service_from_response(mms: bytes) -> bytes:
    """Unwrap a confirmed-ResponsePDU to its service-response TLV.

    Raises :class:`MmsError` for confirmed-ErrorPDU, reject, or anything else.
    """
    tag = mms[0]
    if tag == CONFIRMED_RESPONSE:
        _, body, _ = ber.read_tlv(mms, 0)
        for t, v in ber.iter_tlv(body):
            if t != 0x02:                            # skip invokeID
                return ber.tlv(t, v)
        return b""
    if tag == CONFIRMED_ERROR:
        raise MmsError(decode_service_error(mms))
    if tag == REJECT:
        raise MmsError(f"service rejected: {mms.hex()}")
    raise MmsError(f"unexpected MMS response tag 0x{tag:02x}")


# ==== Initiate ============================================================
def build_initiate() -> bytes:
    """MMS initiate-RequestPDU [8] -- the association parameters we propose."""
    detail = b""
    detail += ber.int_tlv(0x80, 1)                       # proposedVersionNumber = 1
    detail += ber.tlv(0x81, bytes([0x05, 0xF1, 0x00]))   # proposedParameterCBB, unused=5
    # servicesSupportedCalling: unused bits = 3, then the standard client bitmap
    detail += ber.tlv(0x82, bytes([0x03, 0xEE, 0x1C, 0x00, 0x00, 0x04,
                                   0x08, 0x00, 0x00, 0x79, 0xEF, 0x18]))

    body = b""
    body += ber.tlv(0x80, bytes([0x00, 0xFD, 0xE8]))     # localDetailCalling = 65000
    body += ber.int_tlv(0x81, 5)                          # maxServOutstandingCalling = 5
    body += ber.int_tlv(0x82, 5)                          # maxServOutstandingCalled  = 5
    body += ber.int_tlv(0x83, 10)                         # dataStructureNestingLevel = 10
    body += ber.tlv(0xA4, detail)                         # mmsInitRequestDetail
    return ber.tlv(INITIATE_REQUEST, body)


def decode_initiate_response(mms: bytes) -> dict:
    """initiate-ResponsePDU [9] -> the limits the peer negotiated.

    Keys (all optional on the wire, absent ones are simply missing):
    ``localDetailCalled`` -- the largest MMS PDU the *server* will accept, in
    bytes; ``maxServOutstandingCalling`` / ``maxServOutstandingCalled`` --
    how many confirmed requests may be in flight each way;
    ``dataStructureNestingLevel``.

    ``localDetailCalled`` is an MMS-level ceiling and has nothing to do with the
    COTP TPDU size: a relay commonly answers 12000 here while negotiating a
    1024-byte TPDU, and a batching client has to respect both.
    """
    _, body, _ = ber.read_tlv(mms, 0)
    fields = {}
    for t, v in ber.iter_tlv(body):
        name = _INITIATE_RESPONSE_FIELDS.get(t)
        if name is not None and v:
            fields[name] = ber.to_int(v)
    return fields


_INITIATE_RESPONSE_FIELDS = {
    0x80: "localDetailCalled",
    0x81: "maxServOutstandingCalling",
    0x82: "maxServOutstandingCalled",
    0x83: "dataStructureNestingLevel",
}


# ==== GetNameList =========================================================
def build_get_name_list(object_class: int, scope: str, domain=None,
                        continue_after=None) -> bytes:
    req = ber.tlv(0xA0, ber.int_tlv(0x80, object_class))   # objectClass [0]{ basic INT }
    if scope == "vmd":
        req += ber.tlv(0xA1, ber.tlv(0x80, b""))           # scope vmd-specific [0] NULL
    elif scope == "domain":
        req += ber.tlv(0xA1, ber.tlv(0x81, domain.encode()))  # domainSpecific [1] Identifier
    else:
        raise ValueError(scope)
    if continue_after is not None:
        req += ber.tlv(0x82, continue_after.encode())      # continueAfter [2]
    return ber.tlv(SVC_GET_NAME_LIST, req)


def decode_name_list(service_bytes: bytes):
    """getNameList-Response -> (list_of_identifier, more_follows)."""
    _, body, _ = ber.read_tlv(service_bytes, 0)
    names, more = [], False
    for t, v in ber.iter_tlv(body):
        if t == 0xA0:                              # listOfIdentifier [0]
            for it, iv in ber.iter_tlv(v):
                if it == VISIBLE_STRING:
                    names.append(iv.decode("latin-1"))
        elif t == 0x81:                            # moreFollows BOOLEAN
            more = bool(v and v[0])
    return names, more


# ==== Read ================================================================
def read_entry(domain: str, item: str) -> bytes:
    """One ``listOfVariable`` entry: SEQUENCE { variableSpecification name [0] }.

    ``listOfVariable`` is ``[0] IMPLICIT SEQUENCE OF SEQUENCE { ... }``, so N
    variables means this TLV repeated N times -- *not* one SEQUENCE holding N
    names. Getting that wrong is quiet: a relay accepts the malformed PDU and
    answers with a single value.
    """
    return ber.tlv(0x30, ber.tlv(0xA0, object_name(domain, item)))


def build_read(domain: str, item: str) -> bytes:
    """Read one variable of a domain."""
    return build_read_multi(domain, (item,))


def build_read_multi(domain: str, items) -> bytes:
    """Read N variables of one domain in a single request.

    The response is one ``listOfAccessResult`` in the same order,
    :func:`decode_read_response` returns it as a list. Size the batch against
    the negotiated limits (:func:`decode_initiate_response`) -- the request
    itself is one MMS PDU however many variables it names.
    """
    return build_read_refs((domain, it) for it in items)


def build_read_refs(refs) -> bytes:
    """Read N variables that may span domains, from ``(domain, item)`` pairs.

    ObjectName sits inside each ``listOfVariable`` entry, not above the list,
    so naming two logical devices in one Read is the same encoding as naming
    one twice -- :func:`build_read_multi` is this function with the domain
    held constant. The response is still one ``listOfAccessResult`` in
    request order.
    """
    entries = b"".join(read_entry(dom, it) for dom, it in refs)
    spec = ber.tlv(0xA1, ber.tlv(0xA0, entries))           # varAccessSpec [1]{ listOfVariable [0] }
    return ber.tlv(SVC_READ, spec)


def build_read_named_list(domain: str, name: str) -> bytes:
    """Read a whole named variable list (DataSet) by name.

    The other arm of VariableAccessSpecification: ``variableListName [1]
    ObjectName``. ObjectName is a CHOICE, so its tag is explicit -- [1] wraps
    the domain-specific [1] SEQUENCE rather than replacing its tag.
    """
    spec = ber.tlv(0xA1, ber.tlv(0xA1, object_name(domain, name)))
    return ber.tlv(SVC_READ, spec)


# DataAccessError code names (MMS)
_ACCESS_ERR = {
    0: "object-invalidated", 1: "hardware-fault", 2: "temporarily-unavailable",
    3: "object-access-denied", 4: "object-undefined", 5: "invalid-address",
    6: "type-unsupported", 7: "type-inconsistent", 8: "object-attribute-inconsistent",
    9: "object-access-unsupported", 10: "object-non-existent", 11: "object-value-invalid",
}


def decode_read_response(service_bytes: bytes):
    """service_bytes = read-Response [4] (0xA4). Return list of decoded values."""
    _, body, _ = ber.read_tlv(service_bytes, 0)   # unwrap 0xA4
    results = []
    for t, v in ber.iter_tlv(body):
        if t == 0xA1:                             # listOfAccessResult [1]
            for at, av in ber.iter_tlv(v):
                if at == 0x80:                    # failure [0] DataAccessError
                    code = av[0] if av else -1
                    results.append({"error": _ACCESS_ERR.get(code, f"error-{code}")})
                else:                             # success: Data with its own tag
                    results.append(decode_data(at, av))
    return results


# ==== GetVariableAccessAttributes ========================================
def build_get_var_access_attributes(domain: str, item: str) -> bytes:
    return ber.tlv(SVC_GET_VAR_ACCESS_ATTRIBUTES, ber.tlv(0xA0, object_name(domain, item)))


# ==== File services =======================================================
def file_name(path: str) -> bytes:
    """MMS FileName ::= SEQUENCE OF GraphicString; one component holding the
    whole path is what relays expect."""
    return ber.tlv(GRAPHIC_STRING, path.encode("latin-1"))


def build_file_directory(path=None, continue_after=None) -> bytes:
    req = b""
    if path is not None:
        req += ber.tlv(0xA0, file_name(path))          # fileSpecification [0]
    if continue_after is not None:
        req += ber.tlv(0xA1, file_name(continue_after))  # continueAfter [1]
    return ber.tlv(SVC_FILE_DIRECTORY, req)


def build_file_open(name: str, initial_position: int = 0) -> bytes:
    req = ber.tlv(0xA0, file_name(name))               # fileName [0]
    req += ber.int_tlv(0x81, initial_position)         # initialPosition [1]
    return ber.tlv(SVC_FILE_OPEN, req)


def build_file_read(frsm_id: int) -> bytes:
    return ber.tlv(SVC_FILE_READ, ber.enc_uint(frsm_id))


def build_file_close(frsm_id: int) -> bytes:
    return ber.tlv(SVC_FILE_CLOSE, ber.enc_uint(frsm_id))


class DirEntry:
    """One entry from a FileDirectory response."""

    __slots__ = ("name", "size", "last_modified")

    def __init__(self, name, size, last_modified):
        self.name = name
        self.size = size
        self.last_modified = last_modified

    def __repr__(self):
        lm = self.last_modified or "-"
        return f"{self.size:>12}  {lm:<16}  {self.name}"


def decode_file_directory(service_bytes: bytes):
    """fileDirectory-Response -> (list_of_DirEntry, more_follows)."""
    _, body, _ = ber.read_tlv(service_bytes, 0)          # unwrap BF4D
    entries, more = [], False
    for t, v in ber.iter_tlv(body):
        if t == 0xA0:                                   # listOfDirectoryEntry [0]
            entries.extend(_collect_entries(v))
        elif t == 0x81:                                 # moreFollows BOOLEAN
            more = bool(v and v[0])
    return entries, more


def _collect_entries(buf):
    """Yield DirEntry from buf. A DirectoryEntry is a SEQUENCE whose first child
    is fileName [0] (0xA0); some relays wrap the whole list in one extra
    SEQUENCE, so a 0x30 that starts with another 0x30 is a wrapper to descend
    into."""
    for et, ev in ber.iter_tlv(buf):
        if et != 0x30 or not ev:
            continue
        if ev[0] == 0xA0:                               # a real DirectoryEntry
            yield _decode_dir_entry(ev)
        elif ev[0] == 0x30:                             # wrapper SEQUENCE OF
            yield from _collect_entries(ev)


def _decode_dir_entry(ev):
    name, size, lm = None, 0, None
    for t, v in ber.iter_tlv(ev):
        if t == 0xA0:                                   # fileName [0]
            for gt, gv in ber.iter_tlv(v):
                if gt == GRAPHIC_STRING:
                    name = gv.decode("latin-1")
        elif t == 0xA1:                                 # fileAttributes [1]
            for at, av in ber.iter_tlv(v):
                if at == 0x80:                          # sizeOfFile
                    size = int.from_bytes(av, "big")
                elif at == 0x81:                        # lastModified GeneralizedTime
                    lm = av.decode("latin-1", "replace")
    return DirEntry(name, size, lm)


def decode_file_open(service_bytes: bytes):
    """fileOpen-Response -> (frsm_id, size_or_None)."""
    _, body, _ = ber.read_tlv(service_bytes, 0)
    frsm_id, size = None, None
    for t, v in ber.iter_tlv(body):
        if t == 0x80:                                   # frsmID [0]
            frsm_id = int.from_bytes(v, "big")
        elif t == 0xA1:                                 # fileAttributes [1]
            for at, av in ber.iter_tlv(v):
                if at == 0x80:
                    size = int.from_bytes(av, "big")
    return frsm_id, size


def decode_file_read(service_bytes: bytes):
    """fileRead-Response -> (data_chunk, more_follows). moreFollows DEFAULT TRUE."""
    _, body, _ = ber.read_tlv(service_bytes, 0)
    data, more = b"", True
    for t, v in ber.iter_tlv(body):
        if t == 0x80:                                   # fileData [0] OCTET STRING
            data = v
        elif t == 0x81:                                 # moreFollows [1]
            more = bool(v and v[0])
    return data, more

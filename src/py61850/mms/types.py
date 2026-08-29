"""MMS ``TypeDescription`` -- the shape of a variable, as returned by
GetVariableAccessAttributes (ACSI GetDataDefinition).

Decode only for now. When the MMS server lands (ROADMAP 1.0) the matching
``encode_type_description`` belongs here too: the server answers this service
by rendering a type out of the SCL model, which is the exact inverse.
"""

from ..core import ber

_TYPE_TAGS = {
    0xA1: "array", 0xA2: "structure", 0x83: "boolean", 0x84: "bit-string",
    0x85: "integer", 0x86: "unsigned", 0x87: "floating-point",
    0x89: "octet-string", 0x8A: "visible-string", 0x8C: "binary-time",
    0x90: "mms-string", 0x91: "utc-time",
}


def decode_type_description(td_tag: int, td_value: bytes):
    """Recursively render a TypeDescription into a nested Python structure."""
    name = _TYPE_TAGS.get(td_tag, f"tag-0x{td_tag:02x}")
    if td_tag == 0xA2:                            # structure -> components [1] SEQ OF
        comps = []
        for t, v in ber.iter_tlv(td_value):
            if t == 0xA1:                         # components [1]
                for ct, cv in ber.iter_tlv(v):    # each: SEQUENCE 0x30
                    comps.append(_decode_component(cv))
        return {"structure": comps}
    if td_tag == 0xA1:                            # array
        return {"array": td_value.hex()}
    # Size fields are not all the same ASN.1 type. integer/unsigned carry an
    # Unsigned8; bit-string carries a *signed* Integer32, where a negative width
    # means the size is fixed rather than a maximum. Reading it unsigned turns
    # the 13-bit Quality attribute (0xf3 = -13) into 243.
    if td_tag == 0x84:                            # bit-string -> Integer32
        return {name: int.from_bytes(td_value, "big", signed=True) if td_value else None}
    if td_tag in (0x85, 0x86):                    # integer/unsigned -> Unsigned8
        return {name: int.from_bytes(td_value, "big") if td_value else None}
    return name


def _decode_component(comp_bytes: bytes):
    """A structure component: SEQUENCE { componentName [0] VisibleString, type [1] }."""
    cname, ctype = None, None
    for t, v in ber.iter_tlv(comp_bytes):
        if t == 0x80:                             # componentName [0]
            cname = v.decode("latin-1", "replace")
        elif t == 0xA1:                           # componentType [1] -> TypeSpecification
            for tt, tv in ber.iter_tlv(v):
                ctype = decode_type_description(tt, tv)
    return {"name": cname, "type": ctype}


def decode_data_definition(service_bytes: bytes):
    """service_bytes = getVariableAccessAttributes-Response [6] (0xA6)."""
    _, body, _ = ber.read_tlv(service_bytes, 0)   # unwrap 0xA6
    mms_deletable = None
    typedesc = None
    for t, v in ber.iter_tlv(body):
        if t == 0x80:                             # mmsDeletable BOOLEAN
            mms_deletable = bool(v[0]) if v else None
        elif t == 0xA2:                           # typeDescription [2] -> TypeSpecification
            for tt, tv in ber.iter_tlv(v):
                typedesc = decode_type_description(tt, tv)
    return {"mmsDeletable": mms_deletable, "type": typedesc}

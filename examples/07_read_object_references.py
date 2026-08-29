#!/usr/bin/env python3
"""Read specific data attributes given their IEC 61850 object references.

    python examples/07_read_object_references.py 192.0.2.22
    python examples/07_read_object_references.py 192.0.2.22 \
        MYLD_ANN/ASV3GGIO1.Ind01.stVal \
        MYLD_ANN/ASV3GGIO1.Ind02.stVal

The reference you get from an SCL file or a vendor manual is ACSI-shaped:

    MYLD_ANN / ASV3GGIO1 . Ind01 . stVal
    <-- logical device -> <-LN-> <-DO-> <-DA->

MMS names the same attribute differently -- the dots become '$' AND the
functional constraint is inserted after the logical node:

    domain "MYLD_ANN", item "ASV3GGIO1$ST$Ind01$stVal"
                                              ^^ the FC the reference omits

The FC is a property of the data attribute, not of the reference, so this
example resolves it from the logical device's own name list instead of guessing
-- one GetNameList, reused for every reference.
"""

import sys

from py61850 import MmsClient, decode_data_definition

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.0.2.22"
REFS = sys.argv[2:] or [
    "MYLD_ANN/ASV3GGIO1.Ind01.stVal",
    "MYLD_ANN/ASV3GGIO1.Ind02.stVal",
    "MYLD_ANN/ASV3GGIO1.Ind03.stVal",
]

# IEC 61850-7-2 functional constraints, in the order worth trying: ST (status)
# and MX (measurand) carry almost every value you want to read.
FCS = ("ST", "MX", "CO", "SP", "SG", "SE", "SV", "CF",
       "DC", "EX", "BR", "RP", "LG", "GO", "MS", "US")


def split_reference(ref):
    """'LD/LN.DO.DA' -> ('LD', ['LN', 'DO', 'DA']). No FC yet -- see resolve()."""
    ld, _, rest = ref.partition("/")
    if not rest:
        raise ValueError(f"{ref!r} is not an LD/LN.DO.DA reference")
    return ld, rest.split(".")


def mms_item(parts, fc):
    """(['LN', 'DO', 'DA'], 'ST') -> 'LN$ST$DO$DA'."""
    ln, *tail = parts
    return "$".join([ln, fc] + tail)


def resolve(names, parts):
    """The MMS item name for LN.DO.DA, or None if the LD has no such attribute.

    `names` is the logical device's variable list as a set, so this is a lookup
    per candidate FC, not a request per candidate.
    """
    for fc in FCS:
        item = mms_item(parts, fc)
        if item in names:
            return item
    return None


def main():
    with MmsClient(HOST) as c:
        refs = REFS
        lds = c.get_server_directory()
        if not any(split_reference(r)[0] in lds for r in refs):
            # The default references are from an SEL-411L. On another relay,
            # fall back to a reference every 61850 server has.
            refs = [f"{lds[0]}/LLN0.Beh.stVal"]
            print(f"(default references not on this relay; using {refs[0]})\n")

        name_cache = {}
        for ref in refs:
            ld, parts = split_reference(ref)
            if ld not in name_cache:
                name_cache[ld] = set(c.get_logical_device_directory(ld))

            item = resolve(name_cache[ld], parts)
            if item is None:
                print(f"{ref}\n  not found in {ld} -- check the LN/DO/DA spelling")
                continue

            # 1) The read itself: domain + MMS item name, one value back.
            value = c.read_value(ld, item)
            print(f"{ref}\n  {ld} / {item}\n  = {value!r}")

        # 2) An indication is more than its stVal. Reading the data object one
        #    level up returns stVal, q and t together, in one request -- and the
        #    type definition is what lets you label them.
        ld, parts = split_reference(refs[0])
        do = resolve(name_cache[ld], parts[:-1])
        if do:
            values = c.read_value(ld, do)
            td = decode_data_definition(c.get_data_definition(ld, do))
            members = [m["name"] for m in td["type"]["structure"]]
            print(f"\nwhole data object  {ld}/{do}")
            for name, value in zip(members, values):
                print(f"  {name:<8} {value!r}")

        # 3) When a reference will not resolve, the LD's name list is the
        #    authority on what the relay actually exposes.
        ln = split_reference(refs[0])[1][0]
        siblings = sorted(n for n in name_cache[ld] if n.startswith(ln + "$"))
        print(f"\n{ln} exposes {len(siblings)} names; the first few:")
        for n in siblings[:8]:
            print("   ", n)

        # A name that does not exist is not an exception: a per-object access
        # failure comes back as a value, so a sweep of many points survives it.
        print("\nbad attribute ->",
              repr(c.read_value(ld, mms_item([ln, "NoSuchDO", "stVal"], "ST"))))


if __name__ == "__main__":
    main()

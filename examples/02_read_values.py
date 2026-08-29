#!/usr/bin/env python3
"""Read values, and inspect a variable's type definition.

    python examples/02_read_values.py 192.0.2.22
    python examples/02_read_values.py 192.0.2.22 MYLD_ANN

Demonstrates:
  * read_value()   -> a decoded Python value (the common case)
  * read()         -> raw MMS TLV bytes, decoded manually (the escape hatch)
  * get_data_definition() -> the type/structure of a variable
"""

import sys

from py61850 import MmsClient, decode_read_response, decode_data_definition

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.0.2.22"

# Logical-device names are configuration-specific, not standard: an ABB IED may
# call it "LD0" while an SEL relay names it "MYLD_ANN". So discover it
# from the server rather than hardcoding one. Pass a name as the second argument
# to pick a specific LD.
LD = sys.argv[2] if len(sys.argv) > 2 else None

# The object reference below IS standard — every logical device has an LLN0 with
# a Beh (behaviour) data object, in MMS name form: LN$FC$DO$DA.
ITEM = "LLN0$ST$Beh$stVal"          # behaviour status value (usually 1 = "on")


def main():
    with MmsClient(HOST) as c:
        ld = LD or c.get_server_directory()[0]
        print(f"logical device: {ld}\n")

        # 1) The easy way: one decoded value back.
        value = c.read_value(ld, ITEM)
        print(f"read_value({ld!r}, {ITEM!r}) = {value!r}")

        # 2) The raw way: read() returns MMS TLV bytes; decode them yourself.
        #    decode_read_response returns a list (one entry per requested var).
        raw = c.read(ld, ITEM)
        decoded = decode_read_response(raw)
        print(f"raw bytes: {raw.hex()}")
        print(f"decoded   : {decoded}")

        # A failed access comes back as {'error': '<code-name>'} rather than
        # raising — so you can read many points and inspect failures per-point.
        # (A bad *service* request still raises MmsError; it is only per-object
        # access failures that arrive as values.)
        missing = c.read_value(ld, "LLN0$ST$DoesNotExist$stVal")
        print(f"missing point -> {missing!r}")

        # 3) The type definition of the variable (GetVariableAccessAttributes).
        td = decode_data_definition(c.get_data_definition(ld, "LLN0$ST$Beh"))
        print(f"\ntype of LLN0$ST$Beh: {td}")

        # Reading the whole data object returns its members in declaration
        # order, so the type definition is what lets you label them.
        members = td["type"]["structure"]
        values = c.read_value(ld, "LLN0$ST$Beh")
        print("labelled          :",
              dict(zip((m["name"] for m in members), values)))


if __name__ == "__main__":
    main()

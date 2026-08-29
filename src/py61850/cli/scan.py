#!/usr/bin/env python3
"""
Scan an IEC 61850 relay over MMS -- reproduces what an MMS sniffer shows.

Runs the sequence:
    Initiate  (association)
    GetServerDirectory          -> logical devices
    GetLogicalDeviceDirectory   -> data model of each LD
    GetDataDefinition           -> type of a sample data object
    GetDataValues               -> value of a sample data object
    GetBRCBValues               -> buffered report control blocks
    GetSGCBValues               -> setting group control block

MMS has no "list the logical nodes" service -- 61850-8-1 flattens each logical
device into one namedVariable list -- so ``--nodes`` reduces that list back to
the LN view and can filter it by LN class, name glob or regex.

Pure Python, standard library only.  Usage:
    mms-scan 192.0.2.22
    mms-scan 192.0.2.22 --port 102 --full
    mms-scan 192.0.2.22 --nodes                     # every LN, by LD
    mms-scan 192.0.2.22 --nodes --ln-class MMXU,PTOC
    mms-scan 192.0.2.22 --nodes --ld MYLD_PROT --flat
    py61850 scan 192.0.2.22 --full
"""

import argparse
import sys

from .. import MmsClient, MmsError, decode_read_response, decode_data_definition


def hr(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def labeled_read(c, ld, obj):
    """Read a structured object and pair each value with its component name."""
    values = decode_read_response(c.read(ld, obj))
    val = values[0] if values else None
    try:
        td = decode_data_definition(c.get_data_definition(ld, obj))
        comps = td.get("type", {}).get("structure") if isinstance(td.get("type"), dict) else None
        names = [x["name"] for x in comps] if comps else None
    except Exception:
        names = None
    if names and isinstance(val, list) and len(names) == len(val):
        return dict(zip(names, val))
    return val


def cmd_nodes(c, args):
    """List logical nodes -- the ACSI GetLogicalDeviceDirectory view."""
    nodes = c.find_logical_nodes(ln_class=args.ln_class or None,
                                 ld=args.ld or None,
                                 pattern=args.ln or None,
                                 regex=args.regex,
                                 ignore_case=not args.case_sensitive)
    if not nodes:
        print("no logical nodes matched")
        return

    if args.flat:
        for ln in nodes:
            print(ln.ref)
    else:
        by_ld = {}
        for ln in nodes:
            by_ld.setdefault(ln.ld, []).append(ln)
        for ld, group in by_ld.items():
            hr(f"{ld}  ({len(group)} logical node(s))")
            print(f"  {'LN':<24} {'CLASS':<6} {'PREFIX':<14} INST")
            for ln in group:
                print(f"  {ln.name:<24} {ln.ln_class:<6} {ln.prefix:<14} "
                      f"{ln.instance or '-'}")

    classes = {}
    for ln in nodes:
        classes[ln.ln_class] = classes.get(ln.ln_class, 0) + 1
    lds = {ln.ld for ln in nodes}
    print("\n" + "-" * 70)
    print(f"{len(nodes)} logical node(s) in {len(lds)} logical device(s)")
    print("  by class: " + ", ".join(f"{k}x{v}" for k, v in sorted(classes.items())))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="mms-scan", description="IEC 61850 data-model scan over MMS")
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=102)
    ap.add_argument("--full", action="store_true",
                    help="dump every LD variable (default: summary + samples)")
    ap.add_argument("--timeout", type=float, default=10)
    ap.add_argument("--nodes", "--ln-list", action="store_true",
                    help="list the logical nodes instead of running the scan")

    sel = ap.add_argument_group(
        "logical-node selection (--nodes; an LN must match every filter given)")
    sel.add_argument("--ln-class", action="append", metavar="CLASS[,CLASS...]",
                     help="LN classes to list, e.g. --ln-class MMXU,PTOC,LGOS")
    sel.add_argument("--ln", metavar="GLOB",
                     help="glob on the LN name, e.g. --ln 'ACN*GGIO*'")
    sel.add_argument("--regex", metavar="RE",
                     help="regex searched in the LD/LN reference")
    sel.add_argument("--ld", action="append", metavar="LD[,LD...]",
                     help="limit to these logical devices (default: all of them)")
    sel.add_argument("-s", "--case-sensitive", action="store_true",
                     help="match names case-sensitively (default: ignore case)")
    sel.add_argument("--flat", action="store_true",
                     help="print one LD/LN reference per line, ungrouped")
    args = ap.parse_args(argv)

    args.ln_class = [x.strip() for group in (args.ln_class or [])
                     for x in group.split(",") if x.strip()]
    args.ld = [x.strip() for group in (args.ld or [])
               for x in group.split(",") if x.strip()]

    c = MmsClient(args.host, args.port, timeout=args.timeout)

    hr(f"Initiate (association) -> {args.host}:{args.port}")
    c.connect()
    print("  MMS association established (initiate-ResponsePDU received)")

    if args.nodes:
        try:
            cmd_nodes(c, args)
        finally:
            c.close()
        return 0

    # ---- GetServerDirectory ----
    hr("GetServerDirectory  (MMS GetNameList: class=domain, scope=vmd)")
    lds = c.get_server_directory()
    for ld in lds:
        print("  LD:", ld)

    for ld in lds:
        # ---- GetLogicalDeviceDirectory ----
        hr(f"GetLogicalDeviceDirectory  {ld}")
        variables = c.get_logical_device_directory(ld)
        datasets = c.get_data_set_directory(ld)
        print(f"  {len(variables)} named variables, {len(datasets)} data sets")
        if args.full:
            for v in variables:
                print("   ", v)
        else:
            for v in variables[:8]:
                print("   ", v)
            if len(variables) > 8:
                print(f"    ... (+{len(variables) - 8} more; use --full)")
        for d in datasets:
            print("   DataSet:", d)

        # sample data object = the first LN's Beh, else first 2-level object
        sample = _first_data_object(variables)
        if sample:
            hr(f"GetDataDefinition  {ld}/{sample}")
            td = decode_data_definition(c.get_data_definition(ld, sample))
            _print_type(td)

            hr(f"GetDataValues (Read)  {ld}/{sample}")
            print("  ", labeled_read(c, ld, sample))

        # ---- GetBRCBValues ----
        brcbs = _control_blocks(variables, "$BR$")
        for name in brcbs:
            hr(f"GetBRCBValues  {ld}/{name}")
            print("  ", _label_rcb(c, ld, name))

        # ---- GetSGCBValues ----
        for sg in _control_blocks(variables, "$SP$", leaf="SGCB"):
            hr(f"GetSGCBValues  {ld}/{sg}")
            print("  ", labeled_read(c, ld, sg))

    c.close()
    print("\nDone.")


def _first_data_object(variables):
    """Pick a representative 2-level data object (LN$FC$DO) to sample."""
    for v in variables:
        if v.count("$") == 2:
            return v
    return None


def _control_blocks(variables, fc, leaf=None):
    out = []
    seen = set()
    for v in variables:
        parts = v.split("$")
        if fc in v and len(parts) >= 3:
            cb = "$".join(parts[:3])
            if leaf and parts[2] != leaf:
                continue
            if cb not in seen:
                seen.add(cb)
                out.append(cb)
    return out


# Standard BRCB member order (IEC 61850-8-1) for labeling
_BRCB_MEMBERS = ["RptID", "RptEna", "DatSet", "ConfRev", "OptFlds", "BufTm",
                 "SqNum", "TrgOps", "IntgPd", "GI", "PurgeBuf", "EntryID",
                 "TimeOfEntry", "ResvTms", "Owner"]


def _label_rcb(c, ld, name):
    vals = decode_read_response(c.read(ld, name))
    v = vals[0] if vals else None
    if isinstance(v, list) and len(v) <= len(_BRCB_MEMBERS):
        return dict(zip(_BRCB_MEMBERS, v))
    return v


def _print_type(td):
    t = td.get("type")
    print("  mmsDeletable:", td.get("mmsDeletable"))
    if isinstance(t, dict) and "structure" in t:
        for comp in t["structure"]:
            print(f"    {comp['name']:<12} : {comp['type']}")
    else:
        print("  type:", t)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)

# py61850 — IEC 61850 toolkit (pure Python)

An importable library (and its CLIs) for talking to an IEC 61850 IED over MMS
(ISO 9506) on TCP port 102 — scan the data model, read values, transfer files.
**No external dependencies** — Python standard library only. The full OSI stack
is hand-built:

```
TCP → TPKT (RFC1006) → COTP (ISO8073) → ISO Session → ISO Presentation
    → ACSE (AARQ/AARE) → MMS (Initiate + confirmed services)
```

See [ROADMAP.md](ROADMAP.md) for what's next (an MMS sniffer, MMS simulation
from SCL, and a GOOSE sniff/publish interface).

## Install

```bash
pip install -e .          # from a checkout (editable)
# or, once published/tagged:
# pip install py61850
# pip install "git+https://github.com/OWNER/py61850@v0.1.0"
```

Requires Python ≥ 3.9. Installs `py61850` (subcommands) plus `mms-scan` and
`mms-files` for the two MMS drivers directly.

## Use as a library

The public API lives at the top level — import from `py61850`, not from
the submodules:

```python
from py61850 import MmsClient

with MmsClient("192.0.2.22", timeout=10) as c:      # connects; closes on exit
    for ld in c.get_server_directory():                # logical devices
        variables = c.get_logical_device_directory(ld)
        print(ld, len(variables), "variables")
        print(c.read_value(ld, "LLN0$ST$Beh$stVal"))   # decoded Python value
```

Polling many values — one Read may name many variables, so a poll loop costs one
request per batch, not one per point. The batch is sized against the PDU limit
the server negotiated at association time:

```python
with MmsClient("192.0.2.22") as c:
    bits = [f"ACN1GGIO1$ST$Ind{i}$stVal" for i in range(1, 65)]
    values = c.read_many("MYLD_ANN", bits)          # decoded, in order

    for ds in c.get_data_set_directory("MYLD_ANN"): # predefined DataSets
        print(ds, c.read_data_set("MYLD_ANN", ds))  # a whole set in one request
```

A poll list that spans logical devices takes `(ld, item)` pairs instead — the
domain rides in each entry of the request, so one round trip can name several
devices, and the values come back in the order you asked for them:

```python
    c.read_refs([("MYLD_ANN",  "ACN1GGIO1$ST$Ind1$stVal"),
                 ("MYLD_PROT", "LLN0$ST$Beh$stVal")])
```

Logical nodes — MMS answers with the LD's whole flattened variable list, so the
client reduces it back to the LN level and filters it there:

```python
with MmsClient("192.0.2.22") as c:
    for ln in c.find_logical_nodes(["MMXU", "PTOC"]):   # every LD, one request each
        print(ln.ref, ln.ln_class, ln.prefix, ln.instance)

    c.get_logical_nodes("MYLD_PROT")             # one LD, every LN
    c.get_logical_nodes(ld, pattern="G?PTOC*")          # or by name / regex
```

File transfer, with your own progress callback (`progress(got, size)` — wire it
to a bar, a Qt signal, a WebSocket push …):

```python
from py61850 import FileTransfer

with FileTransfer("192.0.2.22") as ft:
    for e in ft.file_directory("/EVENTS/"):            # DirEntry: .name .size .last_modified
        print(e.name, e.size, e.last_modified)
    ft.download_file("/EVENTS/C4_10117.TXT", "out/C4_10117.TXT",
                     progress=lambda got, total: print(f"{got}/{total}"))
```

Errors share one base, so a fleet job catches the whole family at once:

```python
from py61850 import Iec61850Error   # → TransportError | MmsError

try:
    ...
except Iec61850Error as exc:
    log.warning("relay unreachable: %s", exc)
```

`read()` / `get_data_definition()` return **raw MMS TLV bytes**; decode them with
`decode_read_response` / `decode_data_definition` (or use `read_value()` for the
common case). Note: one `MmsClient` owns one socket and is **not thread-safe** —
use one client per thread.

### Public API

| Name | What it is |
|------|-----------|
| `MmsClient` | association + directory / read / data-definition services |
| `FileTransfer` | `MmsClient` + MMS file services (list / search / view / download) |
| `DirEntry` | one file-directory entry (`.name`, `.size`, `.last_modified`) |
| `LogicalNode` | one LN from `find_logical_nodes()` — `.ld`, `.name`, `.ref`, `.prefix`, `.ln_class`, `.instance` |
| `folder_of` | the folder part of an MMS file name, for grouping search hits |
| `Iec61850Error` | base of `TransportError`, `MmsError`, and the reserved `LinkError` / `GooseError` / `SvError` / `SclError` |
| `decode_read_response`, `decode_data_definition`, `decode_service_error` | TLV decoders |

Everything else — `core.ber`, `mms.pdu`, `osi.*`, `CotpTransport` — is internal
and may change between releases.

## Command line

```bash
mms-scan 192.0.2.22            # summary + samples
mms-scan 192.0.2.22 --full     # dump every variable

mms-scan 192.0.2.22 --nodes                      # logical nodes, by LD
mms-scan 192.0.2.22 --nodes --ln-class MMXU,PTOC # only these LN classes
mms-scan 192.0.2.22 --nodes --ld MYLD_PROT --flat

mms-files 192.0.2.22 --list                    # list files
mms-files 192.0.2.22 --view /CFG.TXT           # print a file
mms-files 192.0.2.22 --get  /EVENTS/C4_10117.TXT
mms-files 192.0.2.22 --get-all --filter /EVENTS/ --out relay_files

mms-files 192.0.2.22 --search --ext cfg,dat,hdr   # which folders hold them?
mms-files 192.0.2.22 --search "C4_*.TXT" --root /EVENTS/
mms-files 192.0.2.22 --search --regex "1011[0-9]" --folders
mms-files 192.0.2.22 --get-all --ext cfg,dat --out comtrade
```

`--search` (spelled `--scan` too) walks every file in every folder — the whole
tree, no depth limit — and groups the hits by folder; with no filter it reports
the IED's entire file tree. Its three filters — `--search GLOB` on the file
name, `--ext cfg,dat,hdr` on the extension, `--regex` on the whole path — also
narrow `--list` and `--get-all`, and a file has to match every filter given.
Narrow the walk itself with `--root DIR`, `--depth N` or `--no-recursive`.
`--flat` prints one full path per line for piping; `--folders` prints only the
folders.

MMS has no service that returns logical nodes on their own — 61850-8-1 flattens
each logical device into one `GetNameList` — so `--nodes` reduces that list back
to the LN view (`get_logical_nodes` / `find_logical_nodes` in the library) and
filters it by `--ln-class`, an `--ln GLOB` on the name, or `--regex` on the
`LD/LN` reference. It costs one request per logical device.

`py61850 <command>` reaches the same drivers — `py61850 scan …`,
`py61850 files …` — which is where `goose` and `sv` will attach. Without
installing, `python -m py61850 scan …` works from a checkout.

## Services (ACSI request → MMS service)

| Requested command          | MMS service                             |
|----------------------------|-----------------------------------------|
| Initiate                   | Initiate-Request                        |
| GetServerDirectory         | GetNameList (class=domain, scope=vmd)   |
| GetLogicalDeviceDirectory  | GetNameList (class=namedVariable/List)  |
| GetDataDefinition          | GetVariableAccessAttributes             |
| GetDataValues              | Read                                    |
| GetBRCBValues              | Read on `LLN0$BR$<rcb>`                 |
| GetSGCBValues              | Read on `LLN0$SP$SGCB`                  |
| GetServerDirectory{FILE}   | FileDirectory                           |
| GetFile                    | FileOpen → FileRead* → FileClose        |

Note: a relay's directory-reported file size can be larger than the bytes
actually streamed by FileRead — the download is complete when the relay sets
`moreFollows = FALSE`, which is authoritative.

## Layout

```
src/py61850/
├── __init__.py         # the public API (import from here)
├── errors.py           # the whole exception tree, one file
├── core/               # shared by MMS, GOOSE and SV — pure codec, no I/O
│   ├── ber.py          #   definite-length BER (+ a zero-copy decode path for SV)
│   ├── data.py         #   MMS Data values, encode AND decode
│   ├── quality.py      #   the 13-bit Quality bitstring
│   └── time.py         #   UtcTime / BinaryTime
├── osi/                # the stack under MMS-over-TCP (GOOSE/SV are raw L2)
│   ├── tpkt.py         #   RFC 1006 framing
│   ├── cotp.py         #   ISO 8073 class 0 — the only socket in osi/
│   ├── session.py      #   ISO 8327
│   ├── presentation.py #   ISO 8823
│   ├── acse.py         #   ISO 8650 AARQ/AARE
│   └── stack.py        #   compose the above: associate, wrap, extract
├── mms/
│   ├── pdu.py          #   every builder and decoder — no sockets, ever
│   ├── types.py        #   TypeDescription
│   ├── service_error.py
│   ├── client.py       #   MmsClient / FileTransfer: association + transactions
│   └── services/       #   one mixin per service group
│       ├── directory.py
│       ├── read.py
│       └── files.py
├── link/               # packet-capture sources — planned (ROADMAP 0.2, 2.0)
├── goose/              # GOOSE pub/sub — planned (ROADMAP 2.0)
├── sv/                 # Sampled Values — planned (after GOOSE)
├── scl/                # SCL parsing — planned (ROADMAP 1.0)
└── cli/
    ├── main.py         # `py61850` — subcommand dispatcher
    ├── scan.py         # `mms-scan` — data-model scan driver
    └── files.py        # `mms-files` — file scan / search / download driver
```

Two rules hold the shape together:

- **`core/` and `mms/pdu.py` never touch a socket.** That is what lets the MMS
  server (ROADMAP 1.0) reuse the same codec in the opposite direction, and what
  lets the test suite run with no relay reachable.
- **`link/`, `goose/`, `sv/` are never imported from `py61850/__init__.py`.**
  GOOSE/SV are a non-IP EtherType, which no OS lets a plain socket reach, so
  they need a capture driver — Npcap on Windows, libpcap plus `CAP_NET_RAW` on
  Linux — and a NIC on the station bus. The MMS half must keep installing and
  running anywhere with no driver present. **That requirement is GOOSE's, not
  the library's:** MMS is TCP/IP, so an MMS-only user — client *or* sniffer —
  installs nothing (`link.ip`, `link.pcap` and the TCP-102 proxy are all
  standard library). See [ROADMAP.md](ROADMAP.md) 0.2 and 2.0.

## Tests

```bash
python -m unittest discover -s tests -t .
```

Standard-library `unittest`, no test dependencies, and entirely offline — see
[tests/README.md](tests/README.md).

## Licence

py61850 is dual-licensed.

- **[GNU AGPL v3 or later](LICENSE)** — free to use, study, modify and share.
  If you distribute software built on py61850, or let users reach it over a
  network, the whole work must be released under the AGPL as well, source
  included. Using it inside your own organisation, with nothing published and
  no outside users, costs nothing and requires nothing.
- **[Commercial licence](COMMERCIAL.md)** — for closed-source products or hosted
  services that cannot publish their source under the AGPL. Available from the
  copyright holder on negotiated terms.

Contributions are welcome under the [CLA](CLA.md), which is what keeps that
second option possible — see [CONTRIBUTING.md](CONTRIBUTING.md).

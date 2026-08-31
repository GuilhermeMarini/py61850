# Roadmap — py61850

The library today is a **client**: it associates to a live IED and reads its
data model, values and files over MMS. The roadmap below turns it into a
broader IEC 61850 toolkit — a simulator and a GOOSE stack alongside the client —
while keeping the "pure standard library, deploys anywhere" property.

Status legend: ✅ done · 🔜 next · 🧭 planned · 💡 idea

---

## 0.1 — Library foundation ✅ (done)

The packaging/reuse work that makes everything below importable by other apps.

- ✅ `src/` layout, installable as `py61850` (`pip install -e .`)
- ✅ Public API frozen in `__init__.py` (`MmsClient`, `FileTransfer`, `DirEntry`,
      error types, decoders); internals kept private
- ✅ Unified exception tree: `Iec61850Error` → `TransportError` / `MmsError`
- ✅ Ergonomics: `with MmsClient(...) as c:` context manager, `read_value()`
- ✅ Console scripts `mms-scan` / `mms-files`, `py.typed`, MIT `LICENSE`

## 0.15 — Structure for three protocols ✅ (done)

The flat module list was shaped for one protocol. Everything below needs
different seams, so they were cut before the code arrived rather than after.

- ✅ `core/` — BER, MMS `Data` values, `Quality`, MMS time. Shared by MMS,
      GOOSE and SV, because GOOSE `allData` **is** the MMS `Data` CHOICE and the
      GOOSE/SV envelopes are BER. Pure codec, no I/O.
- ✅ `core.data` gained the **encoders** to match its decoders — the MMS server
      and the GOOSE publisher both need to *build* values, not only read them.
- ✅ `core.ber` gained a zero-copy decode path (`read_tlv_at` / `iter_tlv_view`)
      alongside the copying one, because an SV subscriber decodes thousands of
      frames a second and the per-TLV copy is the cost there.
- ✅ `osi/` — tpkt / cotp / session / presentation / acse / stack, one layer per
      module. Applies to MMS-over-TCP only.
- ✅ `mms/pdu.py` — every builder and decoder, with **no sockets**, so the 1.0
      server can drive it in the opposite direction and so the suite runs offline.
- ✅ `mms/services/` — one mixin per service group, composed in `mms/client.py`.
      Service groups compose; a `FileTransfer(MmsClient)` chain would not once
      write, reports and control land.
- ✅ `link/`, `goose/`, `sv/`, `scl/` reserved as docstring-only packages, and
      kept out of the `py61850` import graph so the MMS client stays
      unprivileged and cross-platform.
- ✅ Offline `unittest` suite (no test deps, no relay) — the payoff of the
      codec/I-O split.
- ✅ `py61850 <command>` CLI dispatcher, so `goose` and `sv` drivers attach
      there instead of as more top-level `mms-*` commands.

## 0.2 — Client hardening 🔜

Make the client dependable enough for unattended fleet jobs to build on.

- ✅ COTP DT fragmentation on send (`osi/cotp.py`). `send()` used to emit one
      oversized DT whatever the payload, so any request past the negotiated
      TPDU size made the relay drop the association — no MMS error, just a
      closed socket on the next `recv()`. `recv()` already reassembled; the
      transport was only asymmetric.
- ✅ Negotiated limits are parsed and exposed: `CotpTransport.negotiated_tpdu_size`
      from the CC, `MmsClient.max_pdu_size` / `max_outstanding` from the
      Initiate-Response. They are different ceilings — an SEL-451 answers 12000
      bytes of MMS PDU over a 1024-byte TPDU — and a batching client owes both.
- ✅ Multi-variable Read and DataSet Read (`read_many`, `read_data_set`).
      Reading a 170-bit logic diagram one variable at a time is 170 requests;
      batched against the negotiated PDU size it is ~12. `read_refs` batches
      `(ld, item)` pairs the same way, so a poll list spanning logical devices
      is one request rather than one per device — the saving is `(K-1) x RTT`
      for K devices, which is noise on a LAN and real on a slow link; the
      point is that callers no longer group by LD and re-order the results.
- 🔜 `write` / `SetDataValues` (MMS Write) — currently read-only.
      Add as `mms/services/write.py`; `core.data.encode_data` is already there.
- 🔜 `MmsClientPool` — per-thread/pooled clients so a web/GUI app can serve
      concurrent requests (one socket + invoke-counter is not thread-safe today)
- 🔜 Auto-reconnect + retry policy for long-running collectors
- 🔜 Recorded-PDU fixtures from real IEDs in `tests/fixtures/` — vendor quirks
      the synthetic tests cannot predict. (The offline suite itself is done.)
- 🧭 Structured logging hooks (opt-in) instead of prints in the CLI layer
- 🧭 Report handling: subscribe to BRCB/URCB and receive InformationReports
      (`mms/services/reports.py`)
- 🧭 **MMS sniffer** — decode a conversation the library is not a party to.
      **Needs nothing installed:** MMS is TCP/IP, so every OS exposes a capture
      path in the box. Sources, all standard library:
      - `link.ip` — raw IPv4 socket (`SIO_RCVALL` on Windows, `SOCK_RAW` on
        Linux). Live, Administrator/`CAP_NET_RAW`, no driver.
      - `link.pcap` — a saved `.pcap`/`.pcapng`. No privileges at all.
      - `mms/proxy.py` — listen on 102, forward to the IED, decode both ways.
        No install *and* no elevation, and the stream is complete and ordered.
      - `link.l2` — optional, only if the user already has libpcap/Npcap for
        GOOSE; buys kernel BPF filtering (`tcp port 102`).

      The decode half is already there and I/O-free: `tpkt.payload_len`,
      `stack.extract_mms_from_response` (its docstring already says
      direction-agnostic), and the `mms.pdu` decoders. What is missing:
      - a TPKT re-framer over a byte stream — `CotpTransport._recv_tpkt` fuses
        the framing loop to its socket, so a sniffer cannot reuse it
      - COTP DT parsing as module-level functions (incl. the EOT bit for
        fragmented DT), lifted out of `CotpTransport`'s methods
      - TCP reassembly: 4-tuple tracking, out-of-order, retransmits, both
        directions — TPKT frames straddle segment boundaries. The real cost.
      - request-direction decoders; today `mms.pdu` is response-shaped. This is
        the same missing half the 1.0 server needs, so build it once.

      Home: `mms/sniffer.py` + `mms/proxy.py`. Pure decode stays in `mms/pdu.py`.

---

## 1.0 — MMS simulation 🧭

**Goal:** read an SCL file (`.scd` / `.cid` / `.icd`) and stand up a virtual IED
on the network that a real client/HMI can associate to and browse — the mirror
image of today's client.

- 🧭 **SCL parser** — `py61850.scl` (package reserved): parse IEDs, LDs, LNs, DOs, DAs,
      DataSets, and Report/Setting control blocks from an SCL XML file into an
      in-memory object model. (Shared by MMS-sim and GOOSE-sim below.)
- 🧭 **Model → MMS server** — a listening `MmsServer` on TCP 102 that answers the
      confirmed services the client already speaks, driven by the SCL model:
      - Initiate / association (server side of `associate.py`)
      - GetNameList (server directory, LD directory, data sets)
      - GetVariableAccessAttributes (type from the SCL model)
      - Read / Write against simulated values
      - FileDirectory / FileOpen/Read/Close over a virtual file store
- 🧭 **Value engine** — static defaults from SCL, plus scripted/animated values
      (ramps, toggles, injected quality/timestamps) for testing HMIs and gateways.
- 💡 Reuse of `core/` and `osi/` as-is: they are direction-agnostic. `mms/pdu.py`
      already holds both directions' primitives (`confirmed_request` /
      `confirmed_response`), so the server mainly adds the request *decoders* and
      response *builders* mirroring what the client uses today. `mms/server.py`
      is the intended home.

**Enables:** HMI/gateway testing without hardware, protocol conformance probing,
training labs, CI that exercises a client against a known model.

---

## 2.0 — GOOSE interface 🧭

**Goal:** a Layer-2 GOOSE stack — both directions — since GOOSE is the other half
of IEC 61850 (fast, multicast, publish/subscribe over raw Ethernet, not MMS/TCP).

- 🧭 **Layer-2 capture** — `py61850.link.l2`, the one source that needs a
      driver. GOOSE/SV are EtherType `0x88B8`/`0x88BA` straight on Ethernet with
      no IP header, and Windows sockets cannot reach a non-IP EtherType at all
      (`SOCK_RAW` there is IP-level only, even with `SIO_RCVALL`) — so unlike
      the MMS sniffer in 0.2, this is not a preference. It is the only way.
      A `ctypes` binding to **libpcap** covers every target from one
      implementation — `wpcap.dll` (Npcap) on Windows, `libpcap.so.1` on Linux,
      `libpcap.dylib` on macOS — where an `AF_PACKET` socket would have covered
      Linux only, and it brings kernel BPF filtering and kernel timestamps.
      `link.ethernet` and `link.pcap` stay pure and driverless, so the whole
      GOOSE decoder can be built and tested offline against a saved capture.
- 🧭 **GOOSE sniffer / subscriber** — `py61850.goose.subscriber`:
      - Open a capture handle on an interface, filter EtherType `0x88B8`
      - Decode the GOOSE PDU (APPID, gocbRef, stNum/sqNum, dataset `allData`,
        timeAllowedToLive, test/simulation flags) — reuses `core.ber` and
        `core.data` unchanged; `allData` members are MMS `Data` values
      - Callback/stream API so an app gets decoded GOOSE messages live
      - Detection helpers: stNum jumps, TAL expiry, dataset shape changes
- 🧭 **GOOSE publisher / simulator** — `py61850.goose.publisher`:
      - Build and transmit GOOSE frames from an SCL-defined GoCB + dataset
        (reuses the 1.0 SCL parser)
      - Correct stNum/sqNum sequencing and retransmission timing (T0/T1 profile)
      - Drive values programmatically to simulate events (trip, block, etc.)

**Notes / constraints**
- 💡 **Npcap is a GOOSE/SV requirement, not a library one.** An MMS-only user —
      client *or* sniffer — installs nothing, ever; see 0.2. It is also a
      *system* prerequisite, not a pip one: `ctypes` is standard library, so
      `pip install py61850` pulls nothing either way.
      - **Windows:** Npcap installed (Wireshark bundles it); Administrator,
        unless Npcap's "restrict to Administrators" option was cleared at
        install. Interfaces are named `\Device\NPF_{GUID}`, so the CLI needs
        an `ifaces` listing that maps them to friendly names.
      - **Linux:** libpcap present, plus `CAP_NET_RAW` on the interpreter.
      - Missing driver must raise `LinkError` naming Npcap, not a bare `OSError`
        out of `CDLL`.
- 💡 GOOSE is multicast and is not routed, unlike the MMS client. The NIC must
      sit on the station bus or a SPAN/mirror port — an office LAN sees
      nothing, and a VM needs its vSwitch in promiscuous/mirroring mode.
- 💡 Publishing rides the same handle (`pcap_sendpacket`), so it needs no
      machinery beyond capture. Timing is best-effort — roughly millisecond
      scheduling accuracy — which suits a simulator or test set, not
      protection-grade retransmission.
- 💡 Keep GOOSE optional: `link`/`goose`/`sv` are already outside the `py61850`
      import graph (asserted by `tests/unit/test_public_api.py`), so the MMS
      client half still installs and runs anywhere with zero privileges and no
      driver. Every privileged, platform-specific line goes in `py61850.link`,
      not in the protocol packages, and the `wpcap.dll` load happens when an
      `l2` capture is opened, not at import — an MMS sniffer importing
      `link.ip` or `link.pcap` must never pull the binding in.
- 💡 `core.ber` does not bounds-check lengths — safe today because TPKT frames
      every MMS PDU, but not on the wire. A truncated or malformed frame must
      raise `GooseError`, not `IndexError`.
- 💡 Sampled Values (SV, EtherType `0x88BA`) is the natural follow-on once the
      GOOSE L2 machinery exists.

---

## Cross-cutting / later 💡

- 💡 `py61850.scl` becomes the shared spine for MMS-sim **and** GOOSE-sim.
- 💡 Higher-level ACSI façade (`Server`, `LogicalDevice`, `DataObject`) over the
      MMS name-string plumbing, so apps address `LD0/LLN0.Beh.stVal` not
      `LLN0$ST$Beh$stVal`.
- 💡 Optional async client (`asyncio`) for large fleet fan-out.
- ❌ **Do not add request pipelining.** Writing N requests before reading their
      responses was measured against an SEL-451 at ~3x *slower* than sequential
      (3 reads: 14.5 ms → 52.1 ms; 6: 28.4 → 91.8; 8: 37.0 → 68.0), even though
      the relay advertises `maxServOutstanding=3`. The extra ~40 ms per cycle is
      the delayed-ACK signature. Batching into one PDU (`read_many`) is the
      lever; concurrency on the one socket is not. Recorded here so it does not
      get proposed again.

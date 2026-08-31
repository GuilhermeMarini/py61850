# Bench verification — open items

Everything in `tests/unit/` runs offline, which is the point of the codec/I-O
split: it proves the bytes we build are the bytes we meant to build. What it
cannot prove is that a *relay* accepts them. This file lists the changes that
are only confirmed against synthetic tests, with the exact check to run at the
bench and what a pass looks like.

Test fleet: `10.165.107.22` (SEL-411L), and the SEL-451-5 the measurements in
issue #1 came from (FID `SEL-451-5-R331-V1-Z033014-D20250919`).

Delete a row once it has been run on hardware; if it fails, the capture belongs
in `fixtures/` (see `fixtures/README.md`) as a regression test.

---

## 1. COTP DT fragmentation over a real association

The blocker from issue #1: `CotpTransport.send()` now splits a payload larger
than the negotiated TPDU size into DT fragments, EOT set only on the last.
Before the fix, ~20 variables (≈1038 B) made the relay close the association
with no MMS error.

```python
from py61850 import MmsClient

with MmsClient("10.165.107.22") as c:
    print(c.t.negotiated_tpdu_size, c.max_pdu_size, c.max_outstanding)
    ld = c.get_server_directory()[0]
    names = c.get_logical_device_directory(ld)
    leaves = [n for n in names if n.count("$") >= 2][:64]
    values = c.read_many(ld, leaves)
    print(len(leaves), "asked,", len(values), "returned")
```

**Pass:** 64 values back, association still up (a following `read_value` works).
The request is ~3.2 kB, so it must leave as 4 DTs over a 1024-byte link — worth
confirming in a capture that the first three have `0x02 F0 00` and the last
`0x02 F0 80`.

**Watch for:** a relay that reassembles fragments but caps the *reassembled*
TSDU well below its advertised `localDetailCalled`. If 64 fails where 18 works,
that is the shape of it — lower `MmsClient.max_pdu_size` before the read to find
the real ceiling and record the number here.

## 2. Negotiated limits, as the hardware actually answers them

`CotpTransport.negotiated_tpdu_size` is parsed from the CC, `max_pdu_size` /
`max_outstanding` from the Initiate-Response. The decoders are tested against
hand-built PDUs only.

**Pass:** the print in §1 shows `1024 12000 3` on the 451. Fill in what the
411L answers:

| Device | FID | negotiated_tpdu_size | max_pdu_size | max_outstanding |
|--------|-----|----------------------|--------------|-----------------|
| SEL-451-5 | `SEL-451-5-R331-V1-Z033014-D20250919` | 1024 | 12000 | 3 |
| SEL-411L | _(to fill)_ | | | |

Also worth proving that raising the proposal is now safe:
`c.t.tpdu_size = 0x0c` before `connect()` — the relay negotiates back down to
1024 in its CC and `negotiated_tpdu_size` must report 1024, not 4096. Before
this change nothing read the CC, so the client believed its own proposal.

## 3. `read_data_set` — the `variableListName` encoding

The one item with no second source. `variableListName [1] ObjectName` is a
tagged CHOICE, so we encode [1] **explicitly** (`A1 { A1 { domainId, itemId } }`)
rather than replacing the ObjectName tag. That reading is from the ASN.1; it has
never been on the wire from here.

```python
with MmsClient("10.165.107.22") as c:
    ld = c.get_server_directory()[0]
    sets = c.get_data_set_directory(ld)          # SEL ships BRDSet01…
    print(sets)
    print(c.read_data_set(ld, sets[0]))
```

**Pass:** values, one per member of the set, in the server's member order.

**Fail modes to tell apart:** a `confirmed-ErrorPDU` with
`type-inconsistent` / `object-access-unsupported` means the relay dislikes the
*encoding* — capture the request bytes and compare against a Wireshark decode.
`object-non-existent` means the name or the domain is wrong, not the encoding.
If it is the encoding, the implicit form (`A1 { domainId, itemId }` directly
inside the varAccessSpec) is the alternative to try, in
`pdu.build_read_named_list`.

## 4. Batching payoff, re-measured

Issue #1 measured 170 bits at 170 requests / 739 ms, and predicted 12 requests /
81 ms once batching works. Re-measure with `read_many` and record the real
number in the ROADMAP entry.

## 5. Mixed-domain Read (`read_refs`)

The ASN.1 puts ObjectName inside each `listOfVariable` entry, so one Read may
name variables in different logical devices. Every capture we have repeats a
single domain, so the mixed form has never been on the wire from here.

```python
with MmsClient("10.165.107.22") as c:
    lds = c.get_server_directory()[:2]
    print(c.read_refs([(ld, "LLN0$ST$Beh$stVal") for ld in lds]))
```

**Pass:** one value per pair, in the order asked, and one request on the wire
(check with a capture, or by counting round trips).

**Fail modes:** a per-entry `object-non-existent` is a wrong name, not the
encoding — the entries are built by the same `read_entry` a single-domain read
uses. An association drop or a PDU-level error on a request that works when
split per LD would mean a server that only accepts one domain per Read; record
it here and fall back to grouping in `read_refs`.

## 6. TCP_NODELAY

Set in `CotpTransport.connect()`, with the measured justification in a comment
there (it was ~0.2 ms on a single read, never a loss). Nothing to verify beyond
"the association still comes up", which §1 covers.

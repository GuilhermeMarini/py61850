# Tests

Standard library `unittest` — no pytest, no test dependencies, matching the
library's zero-dependency rule. Run them from the repo root:

```bash
python -m unittest discover -s tests -t .
python -m unittest tests.unit.test_ber -v        # one module
```

Everything here is **offline**. Nothing opens a socket, so the suite runs in CI
with no relay reachable. That is possible because the codec (`py61850.core`,
`py61850.mms.pdu`) has no I/O in it — the client is the only part that needs a
network, and it is thin.

## What is asserted

- `test_ber` — length/tag edge cases, and that the zero-copy decode path
  (`read_tlv_at` / `iter_tlv_view`) agrees byte-for-byte with the copying one.
- `test_data` — every `encode_*` round-trips through `decode_data`, plus
  hand-written expected bytes so a round-trip that is symmetrically wrong still
  fails.
- `test_pdu` — request builders against expected bytes derived from the ASN.1
  structure, and response decoders against PDUs assembled from those same
  primitives.
- `test_osi` — the session/presentation/ACSE wrapping round-trips, and
  `extract_mms_from_response` survives a payload whose *content* contains 0x61
  (the byte that a naive scan for fully-encoded-data would trip on).

## fixtures/

Recorded PDUs from real IEDs, as raw bytes, one service per file. These are the
regression tests that catch vendor quirks — a relay that wraps its directory
list in an extra SEQUENCE, or omits `moreFollows`. They have to be captured from
hardware; see `fixtures/README.md`.

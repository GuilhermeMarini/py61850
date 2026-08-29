# Recorded PDU fixtures

Raw MMS service-response bytes captured from real IEDs, so decoders can be
tested against what hardware actually sends rather than only against what our
own builders produce.

**Empty for now** — these must come off real hardware; nothing in the library
can synthesise a vendor quirk it does not already know about.

## Capturing one

`MmsClient._transact()` returns exactly the bytes a fixture holds — the
service-response TLV, already unwrapped from Session/Presentation/ACSE:

```python
from py61850 import MmsClient
from py61850.mms import pdu

with MmsClient("192.0.2.22") as c:
    raw = c._transact(pdu.build_get_name_list(pdu.CLASS_DOMAIN, "vmd"))
    open("tests/fixtures/<vendor>_<model>_getnamelist_vmd.bin", "wb").write(raw)
```

Name files `<vendor>_<model>_<service>[_<variant>].bin`, and add the vendor,
firmware version and what makes the capture interesting to the table below.
Prefer captures that exercise a quirk over captures of the happy path.

| File | Device | Firmware | Why it is here |
|------|--------|----------|----------------|
| _(none yet)_ | | | |

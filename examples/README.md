# Examples

Runnable examples for the `py61850` library. Each takes the relay IP as the
first argument (defaulting to the test relay `192.0.2.22`):

```bash
pip install -e .          # from the repo root, once

python examples/01_connect_and_scan.py  192.0.2.22
python examples/02_read_values.py        192.0.2.22
python examples/03_file_transfer.py      192.0.2.22
python examples/04_error_handling.py     192.0.2.22
python examples/05_fleet_inventory.py    192.0.2.22 192.0.2.23
python examples/06_find_files.py         192.0.2.22 cfg dat hdr
python examples/07_read_object_references.py 192.0.2.22 LD/LN.DO.DA ...
```

| File | Shows |
|------|-------|
| `01_connect_and_scan.py` | Context-manager connect; the three directory services (server / logical-device / data-set) |
| `02_read_values.py` | `read_value()` vs raw `read()` + `decode_read_response`; `get_data_definition()` for a variable's type |
| `03_file_transfer.py` | `FileTransfer`: list files (`DirEntry`), search folders by extension/glob/regex with `search_files()`, fetch to memory, download to disk with a progress callback |
| `04_error_handling.py` | The `Iec61850Error` → `TransportError` / `MmsError` family, caught by kind and by base |
| `05_fleet_inventory.py` | A "third app": sweep many relays into a CSV inventory, recording per-host failures instead of aborting |
| `06_find_files.py` | `search_files()` by extension / glob / regex, hits grouped by folder with `folder_of`; streaming `walk_files()` with an `on_error` handler |
| `07_read_object_references.py` | Turning an ACSI reference (`LD/LN.DO.DA`) into the MMS name `LN$FC$DO$DA` — resolving the functional constraint from the LD's name list, then reading the attribute and its whole data object |

Notes:

- The object references in `02` (`LD0`, `LLN0$ST$Beh$stVal`) are common but not
  universal — run `01` first to see the actual logical-device and variable names
  your relay exposes, then substitute.
- `07` takes references on the command line and falls back to `<first LD>/LLN0.Beh.stVal`
  when the ones you pass are not on the relay; its defaults are SEL-411L points.
- `03` and `05` write into `downloads/` and `inventory.csv` under the current
  directory; both are covered by the repo `.gitignore`. `06` only reads.
- `06` takes the extensions to look for after the host — `06_find_files.py
  192.0.2.22 cfg dat hdr` — and defaults to `cfg dat hdr`.

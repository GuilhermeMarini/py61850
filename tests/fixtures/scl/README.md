# The SCL round-trip corpus

The files a zero-edit load-and-export is compared against. A round-trip
guarantee is only worth what the corpus is worth: a document this library
wrote itself would round-trip perfectly and prove nothing. What has to
survive is what **DIGSI** and **SEL Architect** actually write — their
indentation, their attribute order, their comments, their private namespaces,
their unused `xmlns:` declarations.

So three of these four are real station exports, anonymised. The fourth is
hand-written, and says so.

| Fixture | Size | Tool that wrote it | IEDs | Elements | Comments | `xmlns:` at root |
|---|---:|---|---:|---:|---:|---:|
| `sel.scd` | 23.5 MB | AcSELerator Architect | 30 | 433,297 | 29 | 2 |
| `mixed.scd` | 13.5 MB | IEC 61850 System Configurator | 14 | 252,902 | 1 | 7 |
| `siemens.scd` | 7.1 MB | IEC 61850 System Configurator | 14 | 143,257 | 2 | 6 |
| `namespaces.scd` | small | hand-written | — | — | — | — |

44 MB raw, about 2.1 MB once git has compressed them. They are **excluded
from the sdist** — a development artefact is not something `pip install`
should carry — so a suite run from an unpacked sdist skips the checks that
read them.

`mixed.scd` is the interesting one: a DIGSI export containing SEL relays,
which is why both `esel:` and the two Siemens namespaces are declared on its
root. It is the file that proves two unrelated vendors can attach to one
model without either concept entering it.

None of the three has a `Substation` section — none of the source exports
did. A hand-built one belongs to its own coverage fixture, later.

## What was substituted, and what deliberately was not

Anonymisation is **byte-level in-place substitution**, never a parse and
re-serialise. Reparsing would replace the vendor's formatting with
`ElementTree`'s, and the round-trip test would then be measuring our
serialiser against itself.

**Substituted:** the station name, the utility, engineers' Windows account
names, their machine names, and all IP addressing — remapped into the
documentation ranges of RFC 5737 (`192.0.2.0/24`, `198.51.100.0/24`), keeping
each host's last octet and each subnet's distinctness so the addressing plan
still has its shape.

**Kept, on purpose:**

- **IED names.** The SEL and Siemens naming conventions are what the tools'
  heuristics read; anonymising them would remove the thing under test.
- **`toolID` and vendor version strings.** They are behaviour, not identity —
  and they are the record of which tool wrote this formatting.
- **Timestamps, temporary-directory names, MAC addresses, APPIDs.** Not
  identifying; the temp names are already random.
- **`desc` text, indentation, attribute order, comments, everything else.**

The result differs from the vendor's original at the substituted spans and
nowhere else. That is verified, not assumed: `tools/anonymise_scd.py --check`
re-parses both sides and asserts the same element count, the same attribute
count, the same comment count and the same tag sequence.

## Regenerating them

Only a machine holding the vendor originals can. The substitution mapping
names what was substituted, so tracking it would undo the anonymisation it
describes; it lives beside the script as an untracked `tools/*.local.json`.

```bash
python tools/anonymise_scd.py --map tools/scd_identities.local.json          # rewrite
python tools/anonymise_scd.py --map tools/scd_identities.local.json --check  # verify
```

`tests/unit/test_fixture_hygiene.py` is the half of that which everyone can
run: it checks the **shape** of an anonymised file — accounts are `user<n>`,
machines are `PC<n>`, no private addressing survives — and never writes a real
name down in order to look for it.

## Adding a fixture

Anonymise it first, add it to the mapping, and add it to `.gitignore` **by
name**. The `*.scd` rule there is deliberate: an SCD dropped in this tree is
somebody's substation until proven otherwise. A wildcard negation is how an
un-anonymised file reached a sibling repository once.

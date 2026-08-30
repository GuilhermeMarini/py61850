# Contributing to py61850

Bug reports, recorded PDU captures from real IEDs, and design discussion are
welcome with no paperwork at all. Code needs one extra step — see *The CLA*
below, and please read it before you write the patch rather than after.

## Before you open a pull request

py61850 is **dual-licensed** (AGPL-3.0-or-later, plus commercial licences sold
by the copyright holder — see [COMMERCIAL.md](COMMERCIAL.md)). For that to hold,
the project has to be able to relicense every line it ships, so **every code
contribution must be signed off under the [CLA](CLA.md)**:

```
py61850-CLA-1.0 signed-off-by: Your Full Name <you@example.com>
```

in a commit message on the pull request. One signature covers everything you
contribute afterwards. A PR without it cannot be merged, however good the patch
— not a judgement on the code, just a licence the project cannot grant onward.

Contributions that need **no** CLA: issues, bug reports, feature requests,
questions, and recorded PDUs for `tests/fixtures/` (data captured from a device,
not authored code).

## What makes a good patch here

The repository conventions worth knowing before you start; the ones that come
up most:

- **Standard library only.** No runtime dependencies, ever. That is the reason
  this library installs on a locked-down engineering laptop, and it is not
  negotiable without discussion first.
- **`core/` and `mms/pdu.py` do no I/O.** No sockets, no `open()`. The codec is
  the same code an MMS *server* runs in the other direction, and code without
  sockets is testable offline. `mms/client.py` and `osi/cotp.py` are the only
  places a socket belongs.
- **`link/`, `goose/`, `sv/` stay out of the `py61850` import graph.** Raw
  Ethernet needs `CAP_NET_RAW` and Linux; the MMS client must keep installing
  and running unprivileged on any OS. `tests/unit/test_public_api.py` asserts it.
- **New service groups are mixins** in `mms/services/`, composed in
  `mms/client.py` — not subclasses of `MmsClient`, which does not compose past
  one group.
- **Errors derive from `Iec61850Error`**, so a caller can catch the whole family
  at once. Do not raise bare `OSError`/`ValueError` from a new failure path.
- **The library never prints.** User-facing output belongs in `cli/`.
- **Tests come with the code.** A codec change belongs in `tests/unit/`; a
  client change can be driven through the `FakeTransport` in
  `tests/unit/test_client.py`. The suite opens no sockets and must stay that way.
- **New source files carry the SPDX header** the existing files carry.

## Verifying before you push

```bash
python -m unittest discover -s tests -t .        # offline; no relay needed
python -m compileall -q src tests
python -c "import py61850 as m; print(m.__all__)"
```

If a change can only really be confirmed against hardware, add an entry to
[tests/BENCH.md](tests/BENCH.md) saying what to run and what a pass looks like,
rather than leaving it implicit.

## Commit messages

Imperative mood ("Add file write service", not "Added"). Concise subject line;
use the body for the *why* when it is not obvious.

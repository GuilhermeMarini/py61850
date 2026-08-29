"""Packet capture -- the sources a sniffer draws frames from.

**Planned; nothing implemented yet.** See ROADMAP 0.2 (MMS sniffer) and 2.0
(GOOSE).

Every source here presents the same interface -- open, ``recv`` decoded to raw
bytes plus a timestamp, ``close``, and ``send`` where the medium allows it -- so
the decode stacks above (``mms``, ``goose``, ``sv``) do not know or care which
one they are fed by.  What differs between them is only what the operating
system demands before it will hand over the packets.

    ethernet  Ethernet II + 802.1Q header encode/decode -- pure bytes, no I/O
    pcap      .pcap/.pcapng read/write -- pure stdlib, no driver, any OS
    ip        driverless live capture of IPv4 traffic: a raw socket with
              ``SIO_RCVALL`` on Windows, ``SOCK_RAW`` on Linux.  Reaches MMS
              (TCP 102); cannot reach a non-IP EtherType, so not GOOSE/SV.
    l2        libpcap-backed live capture *and* transmit at Layer 2 -- the only
              source that reaches GOOSE/SV, and an optional faster path for MMS
    _libpcap  the ctypes binding behind ``l2``

Which source a given protocol needs
-----------------------------------
This is the whole point of the split, so it is worth stating flatly:

- **MMS needs nothing installed.**  MMS is TCP/IP, and every OS exposes IP-level
  capture in the box.  An MMS-only application uses ``ip`` (live), ``pcap`` (a
  saved capture), or the TCP-102 forwarding proxy in :mod:`py61850.mms` -- all
  standard library, no driver, ever.  The proxy lives there and not here on
  purpose: it captures nothing -- it is an ordinary TCP listener that happens
  to speak port 102 -- so it belongs with the protocol it forwards.
- **GOOSE and SV need libpcap/Npcap.**  They are EtherType 0x88B8/0x88BA
  directly on Ethernet, with no IP header.  Windows sockets cannot reach a
  non-IP EtherType at all -- ``SOCK_RAW`` there is IP-level only, even with
  ``SIO_RCVALL`` -- so a kernel driver is not a preference, it is the only way.
  In practice that is **Npcap**, what Wireshark installs, reached through
  ``wpcap.dll``.

So the driver requirement belongs to GOOSE/SV specifically, not to capture in
general, and a user who never touches GOOSE never installs anything.

Why libpcap, for the L2 source
------------------------------
libpcap is one API on every platform, so a single :mod:`ctypes` binding covers
all of them -- ``wpcap.dll`` on Windows, ``libpcap.so.1`` on Linux,
``libpcap.dylib`` on macOS -- where an ``AF_PACKET`` socket would have served
Linux only, and applications built on this library run on Windows.  It also
brings kernel-side BPF filtering (``ether proto 0x88b8``, or ``tcp port 102``
when it is used for MMS) and kernel timestamps, without which a
timeAllowedToLive measurement means little, and it hands back a pointer into the
driver's buffer: decode in place with :func:`py61850.core.ber.iter_tlv_view` and
the frame is never copied.

``ctypes`` is standard library, so the zero-dependency rule holds -- ``pip
install py61850`` pulls nothing regardless.  libpcap/Npcap is a *system*
prerequisite of the ``l2`` source alone.

Prerequisites, by source
------------------------
    pcap      nothing.  Any OS, no privileges.
    ip        Administrator on Windows; ``CAP_NET_RAW`` on Linux.  No install.
              IPv4 only, and locally-originated packets are unreliable on some
              Windows versions.
    l2        libpcap present -- Npcap on Windows (Administrator, unless its
              "restrict to Administrators" option was cleared at install),
              libpcap plus ``CAP_NET_RAW`` on Linux.

"No install" is not "no privileges": any promiscuous capture needs elevation on
every OS.  The one path that needs neither is the MMS proxy, which is an
ordinary TCP listener.

For GOOSE and SV the NIC must also sit on the station bus or a SPAN/mirror port:
that traffic is multicast and is not routed, so an office LAN sees nothing, and
a VM needs its vSwitch in promiscuous/mirroring mode.

Nothing here may be imported from :mod:`py61850` at package import time.  The
MMS client must keep installing and running unprivileged, on any OS, with no
capture driver present, exactly as it does today -- so this package and
``goose``/``sv`` stay lazy imports the caller reaches for explicitly, and the
``wpcap.dll`` load happens when an ``l2`` capture is opened, not at import.
"""

__all__ = []

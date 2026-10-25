"""Local destinations only. Do not use ipaddress.is_private.

Allowed names and addresses:
localhost, 127.0.0.0/8, ::1, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16.

Refused: link-local, unspecified (0.0.0.0 and ::), documentation
(192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24), multicast, and reserved.
Python 3.9 and 3.14 both report those documentation and link-local
addresses as is_private. ::1 is is_reserved on those versions and is
still allowed, because it is loopback.
"""

from __future__ import annotations

import ipaddress
import unicodedata

_V4_NETS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_LOOPBACK_V6 = ipaddress.ip_address("::1")


def host_is_local(host: str) -> bool:
    """Return True only for a destination that stays on this machine."""
    if not isinstance(host, str) or not host:
        return False
    if any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in host):
        return False
    name = unicodedata.normalize("NFC", host).rstrip(".").lower()
    if name == "localhost":
        return True
    if name.startswith("[") and name.endswith("]") and name.count("[") == 1:
        inner = name[1:-1]
        if not inner or "[" in inner or "]" in inner:
            return False
        name = inner
    if any(char in name for char in "@/\\%"):
        return False
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        return address == _LOOPBACK_V6
    if address.is_link_local or address.is_unspecified or address.is_multicast or address.is_reserved:
        return False
    return any(address in network for network in _V4_NETS)

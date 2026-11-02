"""Client address resolution that cannot be spoofed by sending X-Forwarded-For directly."""

import ipaddress
from collections.abc import Iterable

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_networks(cidrs: Iterable[str]) -> tuple[Network, ...]:
    return tuple(ipaddress.ip_network(c.strip(), strict=False) for c in cidrs if c.strip())


def _is_trusted(address: str, trusted: tuple[Network, ...]) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in net for net in trusted)


def resolve_client_ip(peer: str | None, forwarded_for: str | None, trusted: tuple[Network, ...]) -> str | None:
    """
    X-Forwarded-For is honoured only when the socket peer is a trusted proxy. The header is then walked from
    the right, skipping further trusted hops; the first untrusted address is the client.
    """
    if not peer or not trusted or not forwarded_for or not _is_trusted(peer, trusted):
        return peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return peer
        if not _is_trusted(hop, trusted):
            return hop
    return hops[0] if hops else peer

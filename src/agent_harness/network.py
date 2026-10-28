import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import SecurityError
from .executor import Tool

LOCAL_NETS = tuple(
    ipaddress.ip_network(n)
    for n in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7")
)


def is_local(address):
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip.version == net.version and ip in net for net in LOCAL_NETS)


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str
    address: str
    port: int
    path: str


def check_url(policy, raw, *, inference=False):
    try:
        if not isinstance(raw, str) or not raw or len(raw) > 8192:
            raise ValueError
        if any(ord(c) <= 32 or ord(c) >= 127 for c in raw) or "\\" in raw:
            raise ValueError
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError
        host = parsed.hostname.lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not 1 <= port <= 65535 or "%" in host or host.endswith("."):
            raise ValueError
        if not inference and (host not in policy.network_allow or port not in policy.network_ports):
            raise SecurityError("network_destination_denied")
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        if not addresses:
            raise SecurityError("network_resolution_failed")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
                # IPv6 loopback is not reserved in ipaddress, but mapped addresses are.
                raise SecurityError("network_address_denied")
            if policy.local_only and not is_local(address):
                raise SecurityError("local_only_network_denied")
        return Target(
            parsed.scheme,
            host,
            sorted(addresses)[0],
            port,
            (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
        )
    except SecurityError:
        raise
    except (ValueError, OSError) as exc:
        raise SecurityError("invalid_url") from exc


def transport(target, *, method="GET", body=None, limit=256 * 1024, timeout=15):
    """Connect to the checked IP once; no proxies, redirects or second DNS lookup."""
    connection = http.client.HTTPConnection(target.host, target.port, timeout=timeout)
    sock = None
    try:
        family = socket.AF_INET6 if ":" in target.address else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((target.address, target.port))
        if target.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=target.host)
        connection.sock = sock
        connection.request(
            method,
            target.path,
            body=body,
            headers={"Content-Type": "application/json", "Connection": "close"},
        )
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise SecurityError("http_status_denied")
        data = response.read(limit + 1)
        if len(data) > limit:
            raise SecurityError("response_too_large")
        return data
    except (OSError, http.client.HTTPException) as exc:
        raise SecurityError("network_failed") from exc
    finally:
        connection.close()
        if sock:
            sock.close()


def http_tool(policy):
    def http_get(url):
        return transport(check_url(policy, url)).decode("utf-8")

    schema = {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
        "additionalProperties": False,
    }
    return Tool("http_get", schema, ("network",), http_get, {"url": "url"})

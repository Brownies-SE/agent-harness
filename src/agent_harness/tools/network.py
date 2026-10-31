import errno
import http.client
import ipaddress
import select
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Tool
from agent_harness.core.runtime import check_run

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
    check_run()
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
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if not 1 <= port <= 65535 or "%" in host or host.endswith("."):
            raise ValueError
        if not inference and (host not in policy.network_allow or port not in policy.network_ports):
            raise SecurityError("network_destination_denied")
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        if not addresses:
            raise SecurityError("network_resolution_failed")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if (
                ip.is_link_local
                or ip.is_multicast
                or ip.is_unspecified
                or (ip.is_reserved and not ip.is_loopback)
            ):
                # IPv6 loopback is classified reserved on some supported Python versions.
                raise SecurityError("network_address_denied")
            if policy.local_only and not is_local(address):
                raise SecurityError("local_only_network_denied")
        check_run()
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


def transport(
    target,
    *,
    method="GET",
    body=None,
    limit=256 * 1024,
    timeout=15,
    headers=None,
    response_headers=None,
    stop_when=None,
    on_chunk=None,
):
    """Connect to the checked IP once; no proxies, redirects or second DNS lookup."""
    request_headers = {"Content-Type": "application/json", "Connection": "close"}
    if headers is not None:
        if not isinstance(headers, dict):
            raise SecurityError("invalid_request")
        for key, value in headers.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or key.lower() in {"host", "connection", "content-length", "transfer-encoding"}
                or not key
                or len(key) > 128
                or len(value) > 8192
                or any(ord(c) < 33 or ord(c) > 126 or c == ":" for c in key)
                or any(ord(c) < 32 or ord(c) > 126 for c in value)
            ):
                raise SecurityError("invalid_request")
            request_headers[key] = value
    control = check_run()
    done = threading.Event()
    expired = threading.Event()
    deadline = time.monotonic() + timeout
    connection = http.client.HTTPConnection(target.host, target.port, timeout=timeout)
    sock = None

    def monitor():
        while not done.wait(0.02):
            if time.monotonic() >= deadline or (control and control.stop_reason()):
                expired.set()
                active = sock
                if active is not None:
                    try:
                        active.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        continue
                return

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    try:
        family = socket.AF_INET6 if ":" in target.address else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setblocking(False)
        error = sock.connect_ex((target.address, target.port))
        pending = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR}
        if error and error not in pending:
            raise OSError(error, "connection failed")
        while error:
            check_run()
            if expired.is_set() or time.monotonic() >= deadline:
                raise SecurityError("network_timeout")
            _, ready, failed = select.select([], [sock], [sock], 0.02)
            if ready or failed:
                error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error:
                    raise OSError(error, "connection failed")
        check_run()
        sock.settimeout(timeout)
        if target.scheme == "https":
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            sock = context.wrap_socket(
                sock, server_hostname=target.host, do_handshake_on_connect=False
            )
            sock.do_handshake()
        connection.sock = sock
        connection.request(
            method,
            target.path,
            body=body,
            headers=request_headers,
        )
        response = connection.getresponse()
        if response_headers is not None:
            received = response.getheaders()
            if sum(len(k) + len(v) for k, v in received) > 16384:
                raise SecurityError("response_too_large")
            response_headers.update({k.lower(): v for k, v in received})
            response_headers["__status_code__"] = str(response.status)
        if not 200 <= response.status < 300:
            raise SecurityError("http_status_denied")
        if stop_when is None and on_chunk is None:
            data = response.read(limit + 1)
        else:
            chunks = bytearray()
            total = 0
            while total <= limit:
                chunk = response.read1(min(4096, limit + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise SecurityError("response_too_large")
                check_run()
                if on_chunk is not None:
                    on_chunk(chunk)
                else:
                    chunks.extend(chunk)
                if stop_when is not None and stop_when(bytes(chunks)):
                    break
            data = bytes(chunks)
        if len(data) > limit:
            raise SecurityError("response_too_large")
        check_run()
        if expired.is_set():
            raise SecurityError("network_timeout")
        return data
    except (OSError, http.client.HTTPException) as exc:
        check_run()
        raise SecurityError("network_timeout" if expired.is_set() else "network_failed") from exc
    finally:
        done.set()
        connection.close()
        if sock:
            sock.close()
        watcher.join(timeout=0.1)


def http_tool(policy):
    from agent_harness.models.tools import HttpArguments, TextResult

    def http_get(url):
        return transport(check_url(policy, url)).decode("utf-8")

    return Tool(
        "http_get",
        HttpArguments.model_json_schema(),
        ("network",),
        http_get,
        {"url": "url"},
        HttpArguments,
        TextResult,
    )

"""Optional mDNS advertising so devices reach the appliance at <name>.local.

Replaces the old JSON-file "DNS" that nothing served. Best-effort: if the
`zeroconf` package or the network isn't available, the app still works (clients
just use the IP address instead of the .local name).
"""
import socket

import config

_zc = None  # keep a reference so the registration stays alive


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packets sent; just picks the iface
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


def start_mdns(port: int):
    global _zc
    try:
        from zeroconf import Zeroconf, ServiceInfo
    except ImportError:
        print("[mdns] zeroconf not installed — skipping .local advertising "
              "(connect via IP instead).")
        return

    try:
        ip = _local_ip()
        host = config.MDNS_HOSTNAME
        info = ServiceInfo(
            "_http._tcp.local.",
            f"{host}._http._tcp.local.",
            addresses=[socket.inet_aton(ip)],
            port=port,
            properties={"path": "/"},
            server=f"{host}.local.",
        )
        _zc = Zeroconf()
        _zc.register_service(info)
        print(f"[mdns] advertising http://{host}.local:{port}  (ip: {ip})")
    except Exception as e:
        print(f"[mdns] could not start advertising: {e}")

"""Network mapping core (Phase 3): discover devices on the local segment,
collect link telemetry, and build a reachability map — no root required.

Honest scope: this maps the local L2 segment by reachability (ARP + ping sweep +
mDNS), not physical topology (that needs SNMP/LLDP on managed switches). "Link
quality" is per-host ping RTT plus the host's own WiFi signal (netsh/iw). It is
cross-platform (Windows-first, Linux fallbacks) and scans ONLY the host's own
private /24 — the target is derived from local interfaces, never user-supplied.
"""
import concurrent.futures
import ipaddress
import platform
import re
import socket
import subprocess
import time

IS_WINDOWS = platform.system().lower() == "windows"

# Compact OUI map — IoT/consumer-relevant prefixes (not the full IEEE registry).
OUI = {
    "B827EB": "Raspberry Pi", "DCA632": "Raspberry Pi", "E45F01": "Raspberry Pi",
    "28CDC1": "Raspberry Pi", "D83ADD": "Raspberry Pi", "2CCF67": "Raspberry Pi",
    "B4E62D": "Espressif (ESP)", "240AC4": "Espressif (ESP)", "30AEA4": "Espressif (ESP)",
    "7C9EBD": "Espressif (ESP)", "A4CF12": "Espressif (ESP)", "3C71BF": "Espressif (ESP)",
    "8CAAB5": "Espressif (ESP)", "DC4F22": "Espressif (ESP)", "246F28": "Espressif (ESP)",
    "84CCA8": "Espressif (ESP)", "90380C": "Espressif (ESP)", "ECFABC": "Espressif (ESP)",
    "001CB3": "Apple", "F01898": "Apple", "ACBC32": "Apple", "A4C361": "Apple",
    "3C0754": "Apple", "F0766F": "Apple", "D0817A": "Apple",
    "0050F2": "Microsoft", "000D3A": "Microsoft", "7C1E52": "Microsoft",
    "001A11": "Google", "F4F5E8": "Google", "544E90": "Amazon", "68544C": "TP-Link",
    "50C7BF": "TP-Link", "AC84C6": "TP-Link", "001132": "Synology", "0018E7": "Xiaomi",
    "286C07": "Xiaomi", "34CE00": "Xiaomi",
}


def oui_vendor(mac):
    if not mac:
        return ""
    key = mac.upper().replace(":", "").replace("-", "")[:6]
    return OUI.get(key, "")


def _run(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8).stdout
    except Exception:
        return ""


def local_ipv4():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


def private_subnet():
    """The host's own private /24 (derived, never user-supplied)."""
    ip = local_ipv4()
    try:
        if ipaddress.ip_address(ip).is_private:
            return ipaddress.ip_network(f"{ip}/24", strict=False)
    except ValueError:
        pass
    return None


def default_gateway():
    if IS_WINDOWS:
        # Locale-proof: the default route row "0.0.0.0  0.0.0.0  <gateway> ..."
        out = _run("route print -4 0.0.0.0") or _run("route print")
        m = re.search(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)", out, re.MULTILINE)
        if m and not m.group(1).startswith("0."):
            return m.group(1)
    else:
        out = _run("ip route")
        m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    return None


def arp_table():
    """ip -> mac from the OS ARP cache."""
    out = _run("arp -a") if IS_WINDOWS else (_run("ip neigh") or _run("arp -n"))
    table = {}
    for line in out.splitlines():
        ipm = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
        macm = re.search(r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", line)
        if ipm and macm:
            table[ipm.group(1)] = macm.group(0).lower().replace("-", ":")
    return table


def ping_host(ip, timeout_ms=400):
    if IS_WINDOWS:
        out = _run(f"ping -n 1 -w {timeout_ms} {ip}")
    else:
        out = _run(f"ping -c 1 -W {max(1, timeout_ms // 1000)} {ip}")
    alive = bool(re.search(r"ttl=\d+", out, re.IGNORECASE))
    rtt = None
    m = re.search(r"(?:time|tempo)[=<]\s*(\d+(?:\.\d+)?)\s*ms", out, re.IGNORECASE)
    if m:
        rtt = float(m.group(1))
    return alive, rtt


def ping_sweep(network, workers=64):
    hosts = [str(h) for h in ipaddress.ip_network(network).hosts()]
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(ping_host, ip): ip for ip in hosts}
        for fut in concurrent.futures.as_completed(futs):
            ip = futs[fut]
            try:
                alive, rtt = fut.result()
            except Exception:
                alive, rtt = False, None
            if alive:
                results[ip] = rtt
    return results


DEFAULT_PORTS = (22, 53, 80, 443, 554, 1234, 5000, 8080, 8000, 9000)


def _probe_one(ip, port, timeout=0.35):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return port if s.connect_ex((ip, port)) == 0 else None
    except OSError:
        return None
    finally:
        s.close()


def probe_services(ips, ports=DEFAULT_PORTS, workers=64):
    """Concurrently probe ip×port -> {ip: [open ports]}."""
    result = {ip: [] for ip in ips}
    pairs = [(ip, p) for ip in ips for p in ports]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_probe_one, ip, p): (ip, p) for ip, p in pairs}
        for fut in concurrent.futures.as_completed(futs):
            ip, p = futs[fut]
            if fut.result():
                result[ip].append(p)
    for ip in result:
        result[ip].sort()
    return result


def wifi_link():
    """Host's own WiFi telemetry (signal/SSID/channel/rate), or None if wired."""
    if IS_WINDOWS:
        out = _run("netsh wlan show interfaces")
        if "SSID" not in out:
            return None
        def grab(label):
            m = re.search(rf"{label}\s*:\s*(.+)", out)
            return m.group(1).strip() if m else None
        sig = grab(r"Signal")
        return {
            "ssid": grab(r"\bSSID"),
            "signal": sig,
            "channel": grab(r"Channel"),
            "radio": grab(r"Radio type"),
            "rx_rate": grab(r"Receive rate"),
            "tx_rate": grab(r"Transmit rate"),
        }
    else:
        out = _run("iw dev")
        dev = re.search(r"Interface (\w+)", out)
        if not dev:
            return None
        link = _run(f"iw dev {dev.group(1)} link")
        if "Not connected" in link or not link.strip():
            return None
        def grab(label):
            m = re.search(rf"{label}:\s*(.+)", link)
            return m.group(1).strip() if m else None
        return {
            "ssid": grab("SSID"), "signal": grab("signal"),
            "channel": grab("freq"), "radio": None,
            "rx_rate": grab("rx bitrate"), "tx_rate": grab("tx bitrate"),
        }


def mdns_browse(timeout=2.5):
    """Named devices via mDNS/DNS-SD (best-effort; needs zeroconf)."""
    try:
        from zeroconf import Zeroconf, ServiceBrowser
    except ImportError:
        return []
    found = {}
    types = ["_http._tcp.local.", "_workstation._tcp.local.", "_ssh._tcp.local.",
             "_googlecast._tcp.local.", "_ipp._tcp.local.", "_raop._tcp.local.",
             "_smb._tcp.local."]

    class _L:
        def add_service(self, zc, type_, name):
            try:
                info = zc.get_service_info(type_, name, timeout=1500)
                if info:
                    for addr in info.parsed_addresses():
                        found.setdefault(addr, set()).add(name.split("._")[0])
            except Exception:
                pass
        def update_service(self, *a):
            pass
        def remove_service(self, *a):
            pass

    zc = Zeroconf()
    try:
        for t in types:
            ServiceBrowser(zc, t, _L())
        time.sleep(timeout)
    finally:
        zc.close()
    return [{"address": ip, "names": sorted(n)} for ip, n in found.items()]


def _rtt_changed(ra, rb):
    if ra is None and rb is None:
        return False
    if (ra is None) != (rb is None):
        return True
    return abs(ra - rb) >= 20  # ignore sub-20ms jitter


def diff_scans(old, new):
    """Compare two scan results (keyed by IP) -> added/removed/changed devices."""
    o = {d["ip"]: d for d in (old or {}).get("devices", [])}
    n = {d["ip"]: d for d in (new or {}).get("devices", [])}

    added = [n[ip] for ip in n if ip not in o]
    removed = [o[ip] for ip in o if ip not in n]
    changed = []
    for ip in n:
        if ip not in o:
            continue
        a, b = o[ip], n[ip]
        ch = {}
        if _rtt_changed(a.get("rtt_ms"), b.get("rtt_ms")):
            ch["rtt_ms"] = [a.get("rtt_ms"), b.get("rtt_ms")]
        pa, pb = set(a.get("ports") or []), set(b.get("ports") or [])
        if pa != pb:
            ch["ports"] = {"opened": sorted(pb - pa), "closed": sorted(pa - pb)}
        if (a.get("mac") or "") != (b.get("mac") or ""):
            ch["mac"] = [a.get("mac"), b.get("mac")]  # same IP, new MAC = device swap
        if set(a.get("names") or []) != set(b.get("names") or []):
            ch["names"] = [a.get("names"), b.get("names")]
        if ch:
            changed.append({"ip": ip, "vendor": b.get("vendor") or a.get("vendor"), "changes": ch})

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "counts": {
            "added": len(added), "removed": len(removed), "changed": len(changed),
            "now": len(n), "before": len(o),
        },
    }


def scan(do_mdns=True):
    """Full scan of the local segment -> structured result."""
    started = time.time()
    host_ip = local_ipv4()
    gw = default_gateway()
    subnet = private_subnet()
    wifi = wifi_link()

    alive = ping_sweep(str(subnet)) if subnet else {}
    # Make sure gateway/host are represented even if ICMP-filtered.
    if gw and gw not in alive:
        alive[gw] = None
    arp = arp_table()
    mdns = {d["address"]: d["names"] for d in (mdns_browse() if do_mdns else [])}
    ports_map = probe_services([ip for ip in alive if ip != host_ip])

    devices = []
    llm_host = None
    try:
        from urllib.parse import urlparse
        import config
        llm_host = urlparse(config.LLM_BASE_URL).hostname
    except Exception:
        pass

    for ip in sorted(alive, key=lambda x: tuple(int(p) for p in x.split("."))):
        mac = arp.get(ip, "")
        role = []
        if ip == host_ip:
            role.append("this-host")
        if ip == gw:
            role.append("gateway")
        if llm_host and ip == llm_host:
            role.append("llm")
        devices.append({
            "ip": ip,
            "mac": mac,
            "vendor": oui_vendor(mac),
            "rtt_ms": alive[ip],
            "ports": ports_map.get(ip, []),
            "names": mdns.get(ip, []),
            "roles": role,
        })

    return {
        "ts": started,
        "duration_s": round(time.time() - started, 1),
        "host": {"hostname": socket.gethostname(), "ip": host_ip, "subnet": str(subnet) if subnet else None},
        "gateway": gw,
        "wifi": wifi,
        "device_count": len(devices),
        "devices": devices,
    }

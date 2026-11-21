"""Integração do painel com a rede e a sessão gráfica no Linux."""

import fcntl
import ipaddress
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Network:
    alias: str
    address: str
    subnet: str

    @property
    def label(self):
        return f"{self.alias} · {self.address}"


def data_directory():
    base = Path(os.environ.get("XDG_DATA_HOME", ""))
    if not base.is_absolute():
        base = Path.home() / ".local/share"
    return base / "towerai-servidor"


def system_environment():
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    return env


def list_networks():
    result = subprocess.run(["ip", "-j", "-4", "address", "show", "up", "scope", "global"], check=False,
                            capture_output=True, encoding="utf-8", errors="replace", timeout=5,
                            env=system_environment())
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Falha ao listar interfaces IPv4.")
    networks = []
    for interface in json.loads(result.stdout):
        if "LOOPBACK" in interface.get("flags", []):
            continue
        for address in interface.get("addr_info", []):
            if address.get("family") != "inet":
                continue
            ipv4 = ipaddress.IPv4Interface((address["local"], address["prefixlen"]))
            if not ipv4.ip.is_loopback and not ipv4.ip.is_link_local:
                networks.append(Network(interface["ifname"], str(ipv4.ip), str(ipv4.network)))
    return sorted(networks, key=lambda network: (network.alias, network.address))


def open_folder(folder):
    subprocess.Popen(["xdg-open", str(folder.resolve())], env=system_environment())


@contextmanager
def panel_lock(folder):
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (folder / ".painel.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True

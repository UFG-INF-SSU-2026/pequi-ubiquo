"""Rede e regra de entrada do TowerAI no Windows."""

import base64
import ipaddress
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from main import PORT

RULE = "TowerAI.Marco4.Server.8443"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"


@dataclass(frozen=True)
class Network:
    address: str
    alias: str
    profile: str

    @property
    def label(self):
        return f"{self.alias} · {self.address}"


def powershell(script, *, elevate=False):
    script = "$ProgressPreference='SilentlyContinue'; " + script
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    if elevate:
        script = (
            "$ErrorActionPreference='Stop'; "
            "$process = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') "
            "-Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList "
            f"'-NoProfile','-NonInteractive','-EncodedCommand','{encoded}'; exit $process.ExitCode"
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-OutputFormat", "Text", "-EncodedCommand", encoded],
        capture_output=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=None if elevate else 20,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "O Windows recusou a operação no firewall.")
    return result.stdout.strip()


def list_networks():
    output = powershell(r"""
        [Console]::OutputEncoding = [Text.Encoding]::UTF8
        $profiles = @(Get-NetConnectionProfile)
        $addresses = @(Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred | ForEach-Object {
            $ip = $_
            $network = $profiles | Where-Object InterfaceIndex -eq $ip.InterfaceIndex | Select-Object -First 1
            if ($network -and !$ip.SkipAsSource -and $ip.IPAddress -notmatch '^(127\.|169\.254\.)') {
                @{
                    address = $ip.IPAddress
                    alias = $ip.InterfaceAlias
                    profile = [string]$network.NetworkCategory
                }
            }
        })
        ConvertTo-Json -InputObject $addresses -Compress
    """)
    return sorted((Network(**item) for item in json.loads(output)), key=lambda item: (item.alias, item.address))


def firewall_data(network):
    address = ipaddress.IPv4Address(network.address)
    payload = {"ip": str(address), "alias": network.alias, "program": str(Path(sys.executable).resolve())}
    data = base64.b64encode(json.dumps(payload).encode("ascii")).decode("ascii")
    return f"$settings = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{data}')) | ConvertFrom-Json\n"


def firewall_allowed(network):
    if network.profile != "Private":
        return False
    return powershell(firewall_data(network) + f"""
        $rule = Get-NetFirewallRule -PolicyStore ActiveStore -Name '{RULE}' -ErrorAction SilentlyContinue
        if ($rule -and $rule.Enabled -eq 'True' -and $rule.Profile -eq 'Private' -and
            $rule.Action -eq 'Allow' -and $rule.Direction -eq 'Inbound' -and
            ($rule | Get-NetFirewallApplicationFilter).Program -eq $settings.program -and
            ($rule | Get-NetFirewallPortFilter).LocalPort -eq '{PORT}' -and
            ($rule | Get-NetFirewallPortFilter).Protocol -eq 'TCP' -and
            ($rule | Get-NetFirewallAddressFilter).LocalAddress -eq $settings.ip -and
            ($rule | Get-NetFirewallAddressFilter).RemoteAddress -eq 'LocalSubnet' -and
            ($rule | Get-NetFirewallInterfaceFilter).InterfaceAlias -eq $settings.alias) {{ 'ready' }}
    """) == "ready"


def allow_firewall(network):
    if network.profile != "Private":
        raise ValueError("Defina esta rede de confiança como Privada nas Configurações do Windows e atualize a lista.")
    powershell(firewall_data(network) + f"""
        $ErrorActionPreference = 'Stop'
        try {{
            $ip = Get-NetIPAddress -IPAddress $settings.ip -AddressFamily IPv4 -ErrorAction Stop |
                Where-Object InterfaceAlias -eq $settings.alias | Select-Object -First 1
            if (!$ip) {{ throw 'Endereço não encontrado na interface escolhida.' }}
            $profile = Get-NetConnectionProfile -InterfaceIndex $ip.InterfaceIndex
            if ($profile.NetworkCategory -ne 'Private') {{ throw 'A rede precisa ser privada.' }}
            $params = @{{
                Name = '{RULE}'; Profile = 'Private'; Enabled = 'True'; Direction = 'Inbound'; Action = 'Allow'
                Program = $settings.program; Protocol = 'TCP'; LocalPort = {PORT}; RemoteAddress = 'LocalSubnet'
                LocalAddress = $settings.ip; InterfaceAlias = $settings.alias; EdgeTraversalPolicy = 'Block'
            }}
            if (Get-NetFirewallRule -Name '{RULE}' -ErrorAction SilentlyContinue) {{
                Set-NetFirewallRule @params | Out-Null
            }} else {{
                New-NetFirewallRule @params -DisplayName 'TowerAI Servidor (TCP {PORT})' | Out-Null
            }}
        }} catch {{ exit 1 }}
    """, elevate=True)
    if not firewall_allowed(network):
        raise RuntimeError("A regra foi solicitada ao Windows, mas não está ativa. Confira a política de firewall deste PC.")

"""Clientes HTTPS da LAN com CA explícita e sem redirecionamento."""

import json
import ssl
import urllib.request
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def lan_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("Use uma origem HTTPS, com nome ou IP e porta.")
    return value.rstrip("/")


def request_json(server, ca, token, route, data=None, method=None, headers=None):
    context = ssl.create_default_context(cafile=ca)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=context),
    )
    request = urllib.request.Request(
        lan_origin(server) + route,
        data=json.dumps(data).encode() if isinstance(data, dict) else data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    with opener.open(request, timeout=5) as response:
        return json.load(response)

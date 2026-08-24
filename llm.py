"""Safe, localhost-first client for an OpenAI-compatible local LLM server.

Distilled from the old app.py `send_chat_with_fallback`, with the network
scanning and hardcoded LAN IPs removed. It only ever talks to a configured
endpoint plus localhost — it never probes the network for "some LLM".
"""
import requests
from requests.exceptions import RequestException

import config


def _endpoints():
    seen, out = set(), []
    for u in (config.LLM_BASE_URL, "http://127.0.0.1:1234", "http://localhost:1234"):
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def chat_completion(messages, temperature=0.7, timeout=(3, 90)):
    """Return the assistant's reply text. Raises RuntimeError if no server answers."""
    payload = {"messages": messages, "temperature": temperature}
    if config.LLM_MODEL and config.LLM_MODEL != "auto":
        payload["model"] = config.LLM_MODEL

    last_err = None
    for base in _endpoints():
        try:
            r = requests.post(
                f"{base}/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except (RequestException, KeyError, IndexError) as e:
            last_err = e
            continue
    raise RuntimeError(f"Local LLM unavailable (tried {_endpoints()}): {last_err}")


def health():
    """(ok, base_url, [model_ids]) — honest check that hits /v1/models."""
    for base in _endpoints():
        try:
            r = requests.get(f"{base}/v1/models", timeout=3)
            if r.ok:
                models = [m["id"] for m in r.json().get("data", [])]
                return True, base, models
        except RequestException:
            continue
    return False, None, []

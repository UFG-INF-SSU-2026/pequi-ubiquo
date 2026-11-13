"""Safe, localhost-first client for an OpenAI-compatible local LLM server.

Distilled from the old app.py `send_chat_with_fallback`, with the network
scanning and hardcoded LAN IPs removed. It only ever talks to a configured
endpoint plus localhost — it never probes the network for "some LLM".
"""
import json

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


class ToolsUnsupported(RuntimeError):
    """The server rejected the `tools` parameter (model without tool calling)."""


def chat(messages, temperature=0.7, timeout=(3, 90), model=None, tools=None):
    """Return the assistant message dict ({"content", maybe "tool_calls"}).
    Raises ToolsUnsupported if `tools` were rejected, RuntimeError if no server answers."""
    payload = {"messages": messages, "temperature": temperature}
    model = model or config.LLM_MODEL
    if model and model != "auto":
        payload["model"] = model
    if tools:
        payload["tools"] = tools

    last_err = None
    for base in _endpoints():
        try:
            r = requests.post(
                f"{base}/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
            if tools and r.status_code in (400, 422, 500) and "tool" in r.text.lower():
                raise ToolsUnsupported(r.text[:300])
            r.raise_for_status()
            data = r.json()
            msg = data["choices"][0]["message"]
            msg["_model"] = data.get("model")  # which model answered (for status checks)
            return msg
        except (RequestException, KeyError, IndexError) as e:
            last_err = e
            continue
    raise RuntimeError(f"Local LLM unavailable (tried {_endpoints()}): {last_err}")


def _sse_lines(r):
    """Yield text lines as soon as bytes arrive. (iter_lines() waits for 512-byte
    blocks, and for non-chunked responses reads to the end before yielding.)"""
    raw = r.raw
    read = getattr(raw, "read1", None)
    buf = b""
    while True:
        data = read(8192, decode_content=True) if read else raw.read(1, decode_content=True)
        if not data:
            break
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.rstrip(b"\r")
            if line:
                yield line.decode("utf-8", errors="replace")
    if buf.strip():
        yield buf.decode("utf-8", errors="replace").strip()


def chat_stream(messages, temperature=0.7, timeout=(3, 90), model=None, tools=None,
                on_delta=lambda text: None):
    """Like chat(), but streams: on_delta(text_piece) is called as content arrives.
    Tool-call fragments are assembled. Returns the same message dict as chat()."""
    payload = {"messages": messages, "temperature": temperature, "stream": True}
    model = model or config.LLM_MODEL
    if model and model != "auto":
        payload["model"] = model
    if tools:
        payload["tools"] = tools

    last_err = None
    for base in _endpoints():
        try:
            r = requests.post(f"{base}/v1/chat/completions", json=payload, timeout=timeout, stream=True)
        except RequestException as e:
            last_err = e
            continue
        with r:
            if tools and r.status_code in (400, 422, 500) and "tool" in r.text.lower():
                raise ToolsUnsupported(r.text[:300])
            r.raise_for_status()
            if "text/event-stream" not in r.headers.get("Content-Type", ""):
                data = r.json()  # server ignored "stream": plain response
                msg = data["choices"][0]["message"]
                msg["_model"] = data.get("model")
                if msg.get("content"):
                    on_delta(msg["content"])
                return msg
            content, calls, model_name = [], {}, None
            for line in _sse_lines(r):
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    data = json.loads(chunk)
                    delta = data["choices"][0].get("delta") or {}
                except (ValueError, KeyError, IndexError):
                    continue
                model_name = data.get("model") or model_name
                if delta.get("content"):
                    content.append(delta["content"])
                    on_delta(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0),
                                            {"id": None, "type": "function",
                                             "function": {"name": "", "arguments": ""}})
                    slot["id"] = tc.get("id") or slot["id"]
                    fn = tc.get("function") or {}
                    slot["function"]["name"] += fn.get("name") or ""
                    slot["function"]["arguments"] += fn.get("arguments") or ""
            msg = {"role": "assistant", "content": "".join(content), "_model": model_name}
            if calls:
                msg["tool_calls"] = [calls[i] for i in sorted(calls)]
            return msg
    raise RuntimeError(f"Local LLM unavailable (tried {_endpoints()}): {last_err}")


def chat_completion(messages, temperature=0.7, timeout=(3, 90), model=None):
    """Return the assistant's reply text. Raises RuntimeError if no server answers."""
    return chat(messages, temperature, timeout, model)["content"] or ""


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

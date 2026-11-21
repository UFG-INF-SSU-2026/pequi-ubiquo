"""Exercita o servidor real por HTTPS com WAV e presença de teste declarada."""

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from uuid import uuid4

from models import MODELS
from pki import issue
from transport import request_json


def load_pcm(path):
    import numpy as np

    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnframes() == 0:
            raise ValueError("Use um WAV PCM16 para o ensaio.")
        rate = wav.getframerate()
        frames = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
        mono = frames.reshape(-1, wav.getnchannels()).mean(axis=1)
    if rate != 16000:
        output_samples = round(len(mono) * 16000 / rate)
        mono = np.interp(np.arange(output_samples) * rate / 16000, np.arange(len(mono)), mono)
    return mono.astype("<i2").tobytes()


def run(args):
    pcm = load_pcm(args.wav)
    with socket.socket() as socket_for_port:
        socket_for_port.bind(("127.0.0.1", 0))
        port = socket_for_port.getsockname()[1]
    server = f"https://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="towerai-ensaio-") as folder:
        folder = Path(folder)
        ca = issue(folder / "tls", ["127.0.0.1"])
        tokens = {role: uuid4().hex for role in ("presence", "microphone")}
        records = {
            role: {"role": role, "room": "ensaio", "sha256": hashlib.sha256(token.encode()).hexdigest()}
            for role, token in tokens.items()
        }
        (folder / "devices.json").write_text(json.dumps(records), encoding="utf-8")
        command = [
            sys.executable, "-B", str(Path(__file__).with_name("main.py")), "--local", str(folder),
            "serve", "--port", str(port), "--device", args.device, "--model", args.model,
            "--cache", str(args.cache.resolve()), "--text-url", args.text_url, "--text-model", args.text_model,
        ]

        def call(path, data=None, role="microphone", method=None, headers=None):
            return request_json(server, str(ca), tokens[role], "/v1" + path, data, method, headers)

        def wait_for_status():
            for _ in range(100):
                try:
                    return call("/status")
                except OSError:
                    time.sleep(0.15)
            raise RuntimeError("A API local não abriu a porta do ensaio.")

        capture_path = "/captures/" + uuid4().hex
        silence = bytes(32000)
        timer = time.perf_counter()
        with subprocess.Popen(command, env={**os.environ, "PYTHONUNBUFFERED": "1"}) as process:
            try:
                wait_for_status()
                call("/presence", {"eventId": uuid4().hex, "observedAt": time.time(), "people": 2}, role="presence")
                call(capture_path, {"title": "Ensaio local com voz de teste"}, method="PUT")
                for seq, offset in enumerate(range(0, len(pcm), len(silence))):
                    block = pcm[offset:offset + len(silence)].ljust(len(silence), b"\0")
                    response = call(f"{capture_path}/chunks/{seq}", block, method="PUT", headers={
                        "Content-Type": "application/octet-stream", "X-Captured-At": str(time.time()),
                    })
                    if response["state"] == "FECHADA":
                        break
                finish = call(capture_path + "/finish", b"", method="PUT")
                if finish["processing"] == "DESCARTADA":
                    raise RuntimeError("O áudio não abriu a reunião. Use um trecho audível, com fala sustentada.")
                processing = time.perf_counter()
                while time.perf_counter() < processing + 250:
                    result = call(capture_path)
                    if result["processing"] == "FALHOU":
                        raise RuntimeError(result["error"])
                    if result["processing"] == "PRONTA":
                        break
                    time.sleep(0.4)
                else:
                    raise RuntimeError("Processamento não terminou no limite do ensaio.")
            finally:
                process.terminate()
                process.wait(timeout=10)

        with subprocess.Popen(command) as restarted:
            try:
                wait_for_status()
                recovered = call(capture_path)
                if recovered["minutes"] != result["minutes"] or recovered["transcript"] != result["transcript"]:
                    raise RuntimeError("Transcrição ou ata divergiram após o reinício.")
                return {
                    "mode": "WAV reproduzido via HTTPS, presença injetada, motores reais locais",
                    "whisper": args.model, "device": args.device, "textModel": args.text_model,
                    "audioSeconds": recovered["audioSeconds"],
                    "elapsedSeconds": round(time.perf_counter() - timer, 2),
                    "persistedAfterRestart": True, "transcript": recovered["transcript"],
                    "minutes": recovered["minutes"],
                }
            finally:
                restarted.terminate()
                restarted.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--cache", type=Path, default=Path("modelos"))
    parser.add_argument("--model", choices=MODELS, default="large-v3-turbo")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--text-url", default="http://127.0.0.1:18082")
    parser.add_argument("--text-model", default="qwen2.5-7b")
    parser.add_argument("--output", type=Path)
    opts = parser.parse_args()
    evidence = json.dumps(run(opts), ensure_ascii=False, indent=2) + "\n"
    if opts.output:
        opts.output.write_text(evidence, encoding="utf-8")
    print(evidence)

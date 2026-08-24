"""Cliente IoT de exemplo para o microserviço de transcrição do TowerAI.

Simula um dispositivo de rede (ESP32/Raspberry Pi) enviando áudio e recebendo a
transcrição, autenticando com uma chave de API (não login de navegador).

Uso:
    # Lote: envia um WAV inteiro e recebe o texto
    python examples/iot_client.py batch  --key tk_... --wav clip.wav [--url http://HOST:5000] [--room general]

    # Streaming: envia frames PCM16 16kHz em tempo real e recebe legendas
    python examples/iot_client.py stream --key tk_... --wav clip16k.wav [--url ...] [--room general]

Sem --wav, gera 3s de silêncio como placeholder (para testar o encanamento).
O WAV para 'stream' deve ser mono 16-bit 16kHz. Para 'batch' qualquer taxa serve.
Requer: requests e simple-websocket (já são dependências do servidor).
"""
import argparse
import io
import struct
import time
import wave

import requests


def load_wav(path):
    if path:
        with wave.open(path, "rb") as w:
            assert w.getnchannels() == 1 and w.getsampwidth() == 2, "WAV precisa ser mono 16-bit"
            pcm = w.readframes(w.getnframes())
            rate = w.getframerate()
        with open(path, "rb") as f:
            return pcm, rate, f.read()
    # placeholder: 3s de silêncio a 16kHz
    frames = struct.pack("<48000h", *([0] * 48000))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(frames)
    return frames, 16000, buf.getvalue()


def batch(args):
    _, _, wav_bytes = load_wav(args.wav)
    params = {"room": args.room} if args.room else {}
    r = requests.post(
        f"{args.url}/api/stt/transcribe",
        params=params,
        data=wav_bytes,
        headers={"Authorization": f"Bearer {args.key}", "Content-Type": "audio/wav"},
        timeout=60,
    )
    print("HTTP", r.status_code)
    print(r.json())


def stream(args):
    import simple_websocket
    pcm, rate, _ = load_wav(args.wav)
    if rate != 16000:
        raise SystemExit("Para streaming, use um WAV mono 16-bit a 16kHz.")
    scheme = "wss" if args.url.startswith("https") else "ws"
    host = args.url.split("://", 1)[1]
    ws_url = f"{scheme}://{host}/api/stt/stream?key={args.key}" + (f"&room={args.room}" if args.room else "")
    ws = simple_websocket.Client(ws_url)
    print(f"conectado a {ws_url}")

    step = int(0.1 * 16000) * 2  # frames de 0.1s (bytes)
    for i in range(0, len(pcm), step):
        ws.send(pcm[i:i + step])
        try:
            while True:
                m = ws.receive(timeout=0)
                if m is None:
                    break
                print("evento:", m)
        except Exception:
            pass
        time.sleep(0.1)

    deadline = time.time() + 3
    while time.time() < deadline:
        m = ws.receive(timeout=2)
        if m is None:
            continue
        print("evento:", m)
    ws.close()
    print("fim do streaming")


def main():
    ap = argparse.ArgumentParser(description="Cliente IoT de exemplo (TowerAI STT)")
    ap.add_argument("mode", choices=["batch", "stream"])
    ap.add_argument("--key", required=True, help="chave de API do dispositivo (tk_...)")
    ap.add_argument("--wav", help="arquivo WAV (mono 16-bit; 16kHz para stream)")
    ap.add_argument("--url", default="http://127.0.0.1:5000", help="URL do servidor TowerAI")
    ap.add_argument("--room", default="general", help="sala para publicar as legendas")
    args = ap.parse_args()
    (batch if args.mode == "batch" else stream)(args)


if __name__ == "__main__":
    main()

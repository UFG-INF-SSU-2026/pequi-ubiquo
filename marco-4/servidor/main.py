"""Inicialização e provisionamento local do TowerAI."""

import argparse
import hashlib
import json
import re
import secrets
import threading
from pathlib import Path

from models import LocalMinutes, LocalWhisper, MODELS
from pki import issue, tls_context
from towerai import Api, Database, Device, MeetingProcessor, Server


PORT = 8443


def devices_file(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def set_device(path, args):
    token = update_device(path, args.id, args.role if args.command == "provision" else None,
                          args.room if args.command == "provision" else None)
    if token:
        print(f"Credencial de {args.id} (mostrada uma vez): {token}")
    print("Reinicie o servidor para aplicar o cadastro.")


def update_device(path, device_id, role=None, room=None):
    records = devices_file(path)
    token = None
    if role is None:
        if records.pop(device_id, None) is None:
            raise ValueError("Dispositivo não cadastrado.")
    else:
        token = secrets.token_urlsafe(32)
        records[device_id] = {
            "role": role, "room": room, "sha256": hashlib.sha256(token.encode()).hexdigest(),
        }
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", name) or not entry.get("room")
           for name, entry in records.items()):
        raise ValueError("Identidade/sala inválida no cadastro.")
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)
    return token


def load_devices(path):
    return {entry["sha256"]: Device(name, entry["role"], entry["room"])
            for name, entry in devices_file(path).items()}


def checked_name(value):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", value):
        raise argparse.ArgumentTypeError("Use de 1 a 40 caracteres alfanuméricos, hífen ou sublinhado.")
    return value


def run_loop(action, ending):
    while not ending.is_set():
        try:
            if action():
                continue
        except Exception as error:
            print(f"Operação em espera: {error}", flush=True)
        ending.wait(1)


def serve(args):
    devices = load_devices(args.local / "devices.json")
    if not devices:
        raise ValueError("Cadastre as credenciais dos produtores antes de iniciar.")
    context = tls_context(args.local / "tls")
    db = Database(args.local / "towerai.sqlite3")
    processor = MeetingProcessor(
        db, LocalWhisper(args.model, args.device, args.cache), LocalMinutes(args.text_model, args.text_url),
    )
    ending = threading.Event()
    with Server((args.bind, args.port), Api(db, devices), context) as server:
        db.recover_jobs()
        for action in (db.sweep, processor.run_once):
            threading.Thread(target=run_loop, args=(action, ending), daemon=True).start()
        print(f"TowerAI HTTPS em {args.bind}:{server.server_port} · Whisper {args.model} ({args.device})", flush=True)
        try:
            server.serve_forever()
        finally:
            ending.set()


def model_options(parser):
    parser.add_argument("--model", choices=MODELS, default="large-v3-turbo")
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cpu")
    parser.add_argument("--cache", type=Path, default=Path("modelos"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", type=Path, default=Path("local"))
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser("provision")
    provision.add_argument("id", type=checked_name)
    provision.add_argument("role", choices=("microphone", "presence"))
    provision.add_argument("room", type=checked_name)
    commands.add_parser("revoke").add_argument("id", type=checked_name)
    commands.add_parser("cert").add_argument("hosts", nargs="+")
    prepare = commands.add_parser("prepare-whisper")
    model_options(prepare)
    serving = commands.add_parser("serve")
    model_options(serving)
    serving.add_argument("--bind", default="127.0.0.1")
    serving.add_argument("--port", type=int, default=PORT)
    serving.add_argument("--text-model", default="qwen2.5-7b")
    serving.add_argument("--text-url", default="http://127.0.0.1:18082")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            serve(args)
        elif args.command in ("provision", "revoke"):
            set_device(args.local / "devices.json", args)
        elif args.command == "cert":
            print(issue(args.local / "tls", args.hosts))
        else:
            LocalWhisper(args.model, args.device, args.cache).prepare(download=True)
            print(f"Modelo local preparado: {args.model} ({args.device})")
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()

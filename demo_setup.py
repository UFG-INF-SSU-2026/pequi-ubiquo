"""Semeia dados de demonstração para explorar o TowerAI imediatamente.

Cria um usuário demo, garante a sala 'general', cadastra um dispositivo IoT de
exemplo (mostra a chave de API), grava uma reunião de exemplo e roda um scan de
rede inicial — assim todas as funcionalidades já têm conteúdo ao abrir.

    python demo_setup.py                  # semeia tudo
    python demo_setup.py --download-model # também baixa o modelo pt-BR do Vosk (~32MB)
"""
import argparse

from werkzeug.security import generate_password_hash

import config
import db
import devices

DEMO_USER = "demo"
DEMO_PASS = "demo1234"


def ensure_user():
    if db.get_user_by_name(DEMO_USER):
        print(f"• usuário '{DEMO_USER}' já existe")
        return
    db.create_user(DEMO_USER, generate_password_hash(DEMO_PASS))
    print(f"• usuário criado: {DEMO_USER} / {DEMO_PASS}")


def ensure_device():
    for d in db.list_devices():
        if d["name"] == "demo-esp32":
            print("• dispositivo 'demo-esp32' já existe (chave não é reexibida)")
            return
    _, key = devices.create_device("demo-esp32", "general")
    print(f"• dispositivo IoT 'demo-esp32' criado — chave de API: {key}")


def sample_meeting():
    if db.active_meeting_for_room("general"):
        print("• já há reunião ativa em 'general'")
        return
    uid = db.get_user_by_name(DEMO_USER)["id"]
    mid = db.start_meeting("general", "Reunião de demonstração", uid)
    for spk, txt in [
        ("demo", "bom dia a todos"),
        ("demo-esp32", "sensor da sala conectado"),
        ("demo", "vamos começar a demonstração do TowerAI"),
    ]:
        db.add_segment(mid, spk, txt)
    db.stop_meeting(mid)
    print(f"• reunião de exemplo #{mid} criada com 3 trechos de transcrição")


def initial_scan():
    try:
        import json
        import netmap
        r = netmap.scan(do_mdns=True)
        db.save_netscan(r["ts"], json.dumps(r))
        print(f"• scan de rede inicial: {r['device_count']} dispositivos em {r['duration_s']}s")
    except Exception as e:
        print(f"• scan de rede ignorado: {e}")


def download_model():
    import io
    import urllib.request
    import zipfile
    from pathlib import Path
    dest = Path(config.BASE_DIR) / "models" / "vosk-model-small-pt-0.3"
    if dest.is_dir():
        print("• modelo pt-BR já presente")
        return
    url = "https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip"
    print("• baixando modelo pt-BR (~32MB)...")
    data = urllib.request.urlopen(url, timeout=180).read()
    zipfile.ZipFile(io.BytesIO(data)).extractall(str(Path(config.BASE_DIR) / "models"))
    print(f"• modelo pronto: {dest}")


def main():
    ap = argparse.ArgumentParser(description="Semeia dados de demonstração do TowerAI")
    ap.add_argument("--download-model", action="store_true", help="baixa o modelo pt-BR do Vosk")
    args = ap.parse_args()

    db.init_db()
    if args.download_model:
        download_model()
    ensure_user()
    ensure_device()
    sample_meeting()
    initial_scan()

    print("\n=== Demo pronto ===")
    print(f"Login: {DEMO_USER} / {DEMO_PASS}")
    print(f"Inicie o servidor:  python serve.py")
    print(f"Acesse:             http://localhost:{config.PORT}")


if __name__ == "__main__":
    main()

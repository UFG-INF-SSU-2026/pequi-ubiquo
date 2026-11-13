"""Semeia dados de demonstração para explorar o TowerAI imediatamente.

Cria um usuário demo, garante a sala 'general', cadastra um dispositivo IoT de
exemplo (mostra a chave de API), grava uma reunião de exemplo e roda um scan de
rede inicial — assim todas as funcionalidades já têm conteúdo ao abrir.

    python demo_setup.py                  # semeia tudo
    python demo_setup.py --download-model # também baixa o modelo Whisper (WHISPER_MODEL, padrão "small" ~480MB)
                                          # e os modelos de identificação de falantes (~33MB)
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
    import stt
    dest = stt.local_model_dir()
    if dest.is_dir():
        print(f"• modelo Whisper já presente: {dest}")
        return
    try:
        from faster_whisper import download_model as fw_download
    except ImportError:
        print("• faster-whisper não instalado — rode `pip install faster-whisper`")
        return
    print(f"• baixando modelo Whisper '{config.WHISPER_MODEL}'...")
    try:
        fw_download(config.WHISPER_MODEL, output_dir=str(dest))
    except Exception as e:
        print(f"• falha ao baixar o modelo (sem acesso ao huggingface.co?): {e}")
        return
    print(f"• modelo pronto: {dest}")


def download_tts_voice():
    import tts
    try:
        print(f"• voz de síntese (Piper) pronta: {tts.download_voice()}")
    except Exception as e:
        print(f"• falha ao baixar a voz Piper (sem acesso ao huggingface.co?): {e}")


def download_diarization_models():
    import diarize
    try:
        print(f"• modelos de identificação de falantes prontos: {diarize.download_models()}")
    except Exception as e:
        print(f"• falha ao baixar os modelos de falantes (sem acesso ao github.com?): {e}")


def main():
    ap = argparse.ArgumentParser(description="Semeia dados de demonstração do TowerAI")
    ap.add_argument("--download-model", action="store_true", help="baixa os modelos Whisper e de falantes para uso offline")
    args = ap.parse_args()

    db.init_db()
    if args.download_model:
        download_model()
        download_diarization_models()
        download_tts_voice()
    ensure_user()
    ensure_device()
    sample_meeting()
    initial_scan()

    print("\n=== Demo pronto ===")
    print(f"Login: {DEMO_USER} / {DEMO_PASS}")
    print("Inicie o servidor:  python serve.py")
    print(f"Acesse:             http://localhost:{config.PORT}")


if __name__ == "__main__":
    main()

"""Configuração e operação local do TowerAI Servidor."""

import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from main import PORT, devices_file, load_devices, run_loop, update_device
from models import LocalMinutes, LocalWhisper
from pki import issue, tls_context
from presenca import PERSON_MODEL, PersonDetector, camera_samples
from towerai import Api, Database, Device, MeetingProcessor, Server


LOG = logging.getLogger(__name__)
if sys.platform == "win32":
    DEFAULT_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TowerAI Servidor"
else:
    from linux_desktop import data_directory, system_environment

    DEFAULT_DATA_DIR = data_directory()

DATA_DIR = Path(os.environ.get("TOWERAI_SERVER_DATA_DIR") or DEFAULT_DATA_DIR)
INSTALL_DIR = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
SOURCE_TOOLS = INSTALL_DIR.parent / ".ferramentas"
TEXT_PORT = 18082
TEXT_ALIAS = "towerai-ata"
WHISPER_REPO = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
WHISPER_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.json", "preprocessor_config.json")
QWEN_REPO = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
QWEN_FILE = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
QWEN_SHA256 = "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"


@dataclass
class Settings:
    address: str = ""
    interface: str = ""
    room: str = "sala-204"
    tls_folder: str = "tls"
    camera: str = ""
    whisper_path: str = ""
    text_path: str = ""


def validate_name(value):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", value):
        raise ValueError("Use de 1 a 40 letras, números, hífens ou sublinhados.")
    return value


def usable_whisper(path):
    return all((path / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json"))


def check_gguf(path):
    with path.open("rb") as stream:
        if stream.read(4) != b"GGUF":
            raise ValueError("Selecione um arquivo de modelo GGUF válido.")


class Configuration:
    def __init__(self, folder=DATA_DIR):
        self.folder = Path(folder)
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.local = self.folder / "local"
        self.models = self.folder / "modelos"
        self.settings_file = self.folder / "configuracao.json"
        self.settings = Settings(**json.loads(self.settings_file.read_text(encoding="utf-8"))) if self.settings_file.exists() else Settings()
        self.settings_lock = threading.Lock()

    @property
    def tls(self):
        return self.local / self.settings.tls_folder

    @property
    def devices(self):
        return self.local / "devices.json"

    @property
    def database(self):
        return self.local / "towerai.sqlite3"

    @property
    def whisper(self):
        return Path(self.settings.whisper_path) if self.settings.whisper_path else self.models / "whisper-turbo"

    @property
    def text(self):
        return Path(self.settings.text_path) if self.settings.text_path else self.models / QWEN_FILE

    @property
    def models_ready(self):
        return self.whisper_ready and self.text.is_file()

    @property
    def whisper_ready(self):
        if self.settings.whisper_path:
            return usable_whisper(self.whisper)
        return all((self.whisper / name).is_file() for name in WHISPER_FILES)

    def save(self, **changes):
        with self.settings_lock:
            new_settings = Settings(**{**asdict(self.settings), **changes})
            temporary = self.settings_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(asdict(new_settings), ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.settings_file)
            self.settings = new_settings

    def issue_certificate(self, address, interface):
        if self.certificate_matches(address):
            self.save(address=address, interface=interface)
            return
        folder = f"tls-{uuid4().hex}"
        issue(self.local / folder, [str(ipaddress.IPv4Address(address)), "127.0.0.1"])
        self.save(address=address, interface=interface, tls_folder=folder)

    def certificate_matches(self, address):
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from datetime import datetime, timezone

        if not address or not all((self.tls / name).is_file() for name in ("ca.pem", "server.pem", "server-key.pem")):
            return False
        try:
            cert = x509.load_pem_x509_certificate((self.tls / "server.pem").read_bytes())
            ca = x509.load_pem_x509_certificate((self.tls / "ca.pem").read_bytes())
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            now = datetime.now(timezone.utc)
            cert.verify_directly_issued_by(ca)
            tls_context(self.tls)
            return (ipaddress.ip_address(address) in san.get_values_for_type(x509.IPAddress)
                    and cert.not_valid_before_utc <= now <= cert.not_valid_after_utc
                    and ca.not_valid_before_utc <= now <= ca.not_valid_after_utc)
        except (ValueError, OSError, InvalidSignature, x509.ExtensionNotFound):
            return False

    def fingerprint(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes

        cert = x509.load_pem_x509_certificate((self.tls / "ca.pem").read_bytes())
        return cert.fingerprint(hashes.SHA256()).hex(":").upper()

    def microphones(self):
        return sorted(name for name, device in devices_file(self.devices).items()
                      if device["role"] == "microphone" and device["room"] == self.settings.room)

    def import_installation(self, source):
        source = Path(source)
        if self.local.exists():
            raise ValueError("Importe antes de cadastrar dispositivos ou emitir o primeiro certificado.")
        names = ("ca.pem", "server.pem", "server-key.pem")
        if not all((source / "tls" / name).is_file() for name in names) or not (source / "towerai.sqlite3").is_file():
            raise ValueError("Selecione a pasta local do servidor anterior, com o banco e os certificados.")
        with TemporaryDirectory(dir=self.folder) as staging:
            destination = Path(staging) / "local"
            (destination / "tls").mkdir(parents=True)
            for name in names:
                shutil.copyfile(source / "tls" / name, destination / "tls" / name)
            tls_context(destination / "tls")
            if (source / "devices.json").is_file():
                load_devices(source / "devices.json")
                shutil.copyfile(source / "devices.json", destination / "devices.json")
            with closing(sqlite3.connect((source / "towerai.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)) as original:
                with closing(sqlite3.connect(destination / self.database.name)) as backup:
                    original.backup(backup)
                    if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError("O banco de origem precisa de reparo antes da importação.")
            destination.replace(self.local)

    def import_models(self, whisper, text):
        if whisper and not usable_whisper(Path(whisper)):
            raise ValueError("Selecione a pasta Whisper convertida para CTranslate2 com model.bin, config.json e tokenizer.json.")
        if text:
            check_gguf(Path(text))
        changes = {}
        if whisper:
            changes["whisper_path"] = str(Path(whisper).resolve())
        if text:
            changes["text_path"] = str(Path(text).resolve())
        self.save(**changes)


def download_models(folder, messages):
    """Obtém os pesos em um processo separado, para poder interromper o download."""
    try:
        from huggingface_hub import hf_hub_download, snapshot_download
        from huggingface_hub.utils import disable_progress_bars

        disable_progress_bars()
        config = Configuration(folder)
        config.models.mkdir(parents=True, exist_ok=True)
        if not config.whisper_ready:
            messages.send(("progress", "Baixando Whisper Large v3 Turbo…"))
            path = config.models / "whisper-turbo"
            snapshot_download(WHISPER_REPO, revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
                              local_dir=path, allow_patterns=list(WHISPER_FILES))
            if not all((path / name).is_file() for name in WHISPER_FILES):
                raise ValueError("A pasta do Whisper ficou incompleta. Tente baixar novamente.")
            messages.send(("settings", {"whisper_path": ""}))
        if not config.text.is_file():
            messages.send(("progress", "Baixando Qwen2.5 para as atas…"))
            # O Hub guarda o download parcial entre tentativas. Só publica o GGUF após verificar o arquivo.
            path = Path(hf_hub_download(QWEN_REPO, QWEN_FILE, revision="dd26da4",
                                        local_dir=config.models / ".download-qwen"))
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != QWEN_SHA256:
                path.unlink(missing_ok=True)
                raise ValueError("O arquivo Qwen baixado falhou na verificação SHA-256.")
            path.replace(config.models / QWEN_FILE)
            messages.send(("settings", {"text_path": ""}))
        messages.send(("done", "Modelos disponíveis para iniciar o servidor."))
    except Exception as error:
        messages.send(("error", str(error)))
    finally:
        messages.close()


def camera_devices():
    import cv2
    from cv2_enumerate_cameras import enumerate_cameras

    if sys.platform == "win32":
        return {camera.path.casefold() if camera.path else f"dshow:{camera.name}": camera
                for camera in enumerate_cameras(cv2.CAP_DSHOW)}
    return {camera.path or f"v4l2:{camera.index}": camera for camera in enumerate_cameras(cv2.CAP_V4L2)}


@dataclass(frozen=True)
class CameraReading:
    people: int | None = None
    image: bytes | None = None
    message: str = "Webcam desligada"
    updated: float = 0


class CameraMonitor:
    def __init__(self, publish):
        self.publish = publish
        self.stop_event = threading.Event()
        self.thread = None
        self.reading = CameraReading()

    def start(self, camera):
        self.stop()
        self.stop_event.clear()
        self.reading = CameraReading(message="Abrindo webcam…")
        self.thread = threading.Thread(target=self.run, args=(camera,), name="webcam", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=8)
            if self.thread.is_alive():
                raise RuntimeError("A webcam ainda está respondendo. Tente novamente em instantes.")
            self.thread = None
        self.reading = CameraReading()
        self.publish(time.time(), None)

    def run(self, camera):
        import cv2

        path = INSTALL_DIR / "assets" / PERSON_MODEL.name if getattr(sys, "frozen", False) else PERSON_MODEL
        frame_data = None

        def preview(frame):
            nonlocal frame_data
            frame_data = None
            if frame is not None:
                height, width = frame.shape[:2]
                ratio = min(480 / width, 270 / height, 1)
                size = (max(1, round(width * ratio)), max(1, round(height * ratio)))
                rgb = cv2.cvtColor(cv2.resize(frame, size), cv2.COLOR_BGR2RGB)
                frame_data = f"P6\n{size[0]} {size[1]}\n255\n".encode("ascii") + rgb.tobytes()

        observed_at, previous = 0, object()
        try:
            detector = PersonDetector(path)
            with closing(camera_samples(camera.index, detector.count, backend=camera.backend,
                                        stop=self.stop_event, preview=preview)) as samples:
                for when, people in samples:
                    message = "Sem sinal da webcam" if frame_data is None else (
                        "Contagem indisponível · estabilizando ou pouca luz" if people is None else "Webcam ativa")
                    self.reading = CameraReading(people, frame_data, message, time.monotonic())
                    if people != previous or when - observed_at >= 2:
                        self.publish(when, people)
                        observed_at, previous = when, people
        except Exception:
            LOG.exception("Falha da webcam")
            self.reading = CameraReading(message="Falha na webcam · verifique a câmera e tente novamente")
            self.publish(time.time(), None)


def launch_text_model(path, log_path):
    windows = sys.platform == "win32"
    frozen = getattr(sys, "frozen", False)
    runtime = INSTALL_DIR / "llama" if frozen else SOURCE_TOOLS / ("llama" if windows else "llama-linux")
    binary = runtime / ("llama-server.exe" if windows else "llama-server")
    if not binary.is_file():
        raise ValueError("O executável local do modelo de ata está ausente. Reinstale o TowerAI Servidor.")
    command = [str(binary), "--model", str(path), "--alias", TEXT_ALIAS,
               "--host", "127.0.0.1", "--port", str(TEXT_PORT), "--ctx-size", "8192",
               "--threads", str(min(8, os.cpu_count() or 1)), "--parallel", "1"]
    with log_path.open("w", encoding="utf-8") as log:
        if not windows:
            return subprocess.Popen(command, cwd=runtime, env=system_environment(),
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        return launch_windows_text_model(command, runtime, log, frozen)


def launch_windows_text_model(command, runtime, log, frozen):
    import ctypes

    # O carregador congelado altera a busca de DLLs. O llama.cpp usa as DLLs da própria pasta.
    if frozen:
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        return subprocess.Popen(command, cwd=runtime, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        if frozen:
            ctypes.windll.kernel32.SetDllDirectoryW(sys._MEIPASS)


class DesktopServer:
    def __init__(self, config):
        self.config = config
        self.server = None
        self.http_thread = None
        self.threads = []
        self.text_process = None
        self.ending = threading.Event()
        self.activity = {}
        self.activity_lock = threading.Lock()
        self.camera = CameraMonitor(self.publish_presence)

    @property
    def running(self):
        return self.http_thread is not None and self.http_thread.is_alive()

    def mark_activity(self, device):
        if device.role == "microphone" and device.room == self.config.settings.room:
            with self.activity_lock:
                self.activity[device.id] = time.monotonic()

    def reload_devices(self, device_id):
        if self.server is not None:
            self.server.api.devices = load_devices(self.config.devices)
        with self.activity_lock:
            self.activity.pop(device_id, None)

    def provision(self, device_id):
        token = update_device(self.config.devices, validate_name(device_id), "microphone", self.config.settings.room)
        self.reload_devices(device_id)
        return token

    def revoke(self, device_id):
        if device_id not in self.config.microphones():
            raise ValueError("Microfone não cadastrado nesta sala.")
        update_device(self.config.devices, device_id)
        self.reload_devices(device_id)

    def publish_presence(self, when, people):
        if self.running:
            device = Device("webcam-local", "presence", self.config.settings.room)
            self.server.api.db.presence(device, uuid4().hex, when, people)

    def text_ready(self):
        return self.text_process is not None and self.text_process.poll() is None

    def start(self, report):
        if self.running:
            return
        settings = self.config.settings
        if not self.config.certificate_matches(settings.address):
            raise ValueError("Selecione o endereço atual da rede e gere o certificado HTTPS.")
        if not self.config.microphones():
            raise ValueError("Cadastre um microfone Android nesta sala antes de iniciar.")
        if not self.config.models_ready:
            raise ValueError("Prepare os modelos de transcrição e ata na aba Modelos.")
        check_gguf(self.config.text)
        with socket.socket() as probe:
            if sys.platform == "linux":
                # Conexões encerradas pelo llama.cpp podem continuar em TIME_WAIT após parar o painel.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", TEXT_PORT))
            except OSError as error:
                raise ValueError(f"A porta local {TEXT_PORT} está ocupada. Pare o outro servidor de ata.") from error
        db = Database(self.config.database)
        api = Api(db, load_devices(self.config.devices), self.mark_activity)
        context = tls_context(self.config.tls)
        origin = f"http://127.0.0.1:{TEXT_PORT}"
        self.ending.clear()
        try:
            self.server = Server((settings.address, PORT), api, context)
            self.server.daemon_threads = False
            report("Carregando o modelo de ata…")
            self.text_process = launch_text_model(self.config.text, self.config.folder / "ata.log")
            http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if self.text_process.poll() is not None:
                    raise RuntimeError("O modelo de ata não iniciou. Consulte ata.log. Verifique também a porta local 18082.")
                try:
                    with http.open(origin + "/health", timeout=1) as response:
                        if json.load(response).get("status") == "ok":
                            break
                except (OSError, ValueError):
                    time.sleep(0.4)
            else:
                raise RuntimeError("O modelo de ata demorou para iniciar. Consulte ata.log.")
            whisper = LocalWhisper("large-v3-turbo", "cpu", self.config.models, model_path=str(self.config.whisper))
            report("Carregando o modelo de transcrição…")
            whisper.prepare()
            processor = MeetingProcessor(db, whisper, LocalMinutes(TEXT_ALIAS, origin), log=LOG.error)
            db.recover_jobs()
            with self.activity_lock:
                self.activity.clear()
            self.http_thread = threading.Thread(target=self.server.serve_forever, name="https", daemon=True)
            self.http_thread.start()
            self.threads = [threading.Thread(target=run_loop, args=(action, self.ending), daemon=True)
                            for action in (db.sweep, lambda: self.text_ready() and processor.run_once())]
            for thread in self.threads:
                thread.start()
            self.publish_presence(time.time(), self.camera.reading.people)
        except Exception:
            self.stop()
            raise

    def stop(self):
        try:
            self.camera.stop()
        finally:
            self.ending.set()
            if self.running:
                self.server.shutdown()
                self.http_thread.join()
            if self.server is not None:
                self.server.server_close()
            for thread in self.threads:
                thread.join()
            if self.text_process is not None and self.text_process.poll() is None:
                self.text_process.terminate()
                try:
                    self.text_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.text_process.kill()
                    self.text_process.wait()
            self.server = self.http_thread = self.text_process = None
            self.threads.clear()

    def dashboard(self):
        server = self.server
        if server is None or not self.running:
            return None
        db = server.api.db
        room = self.config.settings.room
        with db.connection() as connection:
            capture = connection.execute(
                "SELECT title, state, level, recorded, last_audio FROM captures WHERE room=? AND ended_at IS NULL", (room,),
            ).fetchone()
            job = connection.execute(
                "SELECT title, processing, error FROM captures WHERE room=? AND ended_at IS NOT NULL "
                "AND processing != 'DESCARTADA' ORDER BY CASE processing "
                "WHEN 'TRANSCREVENDO' THEN 0 WHEN 'GERANDO_ATA' THEN 0 WHEN 'PENDENTE' THEN 1 ELSE 2 END, "
                "ended_at DESC LIMIT 1", (room,),
            ).fetchone()
            pending = connection.execute("SELECT count(*) FROM captures WHERE room=? AND processing='PENDENTE'", (room,)).fetchone()[0]
        with self.activity_lock:
            last_seen = max(self.activity.values(), default=None)
        return capture, job, pending, last_seen, db.policy.audio_timeout, self.text_ready()

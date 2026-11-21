"""API LAN, decisão de sala e processamento persistente do Marco 4."""

import hashlib
import json
import logging
import math
import re
import sqlite3
import ssl
import tempfile
import wave
from array import array
from contextlib import closing, contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from sys import byteorder
import sys
from time import time
from urllib.parse import urlsplit


RATE = 16000
CHUNK_BYTES = RATE * 2
IDENTIFIER = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True)
class Policy:
    voice_dbfs: float = -42.0
    open_seconds: int = 3
    silence_seconds: int = 10
    audio_timeout: int = 6
    suspension_timeout: int = 60
    presence_ttl: int = 15
    max_audio_seconds: int = 600
    waiting_timeout: int = 120


@dataclass(frozen=True)
class Device:
    id: str
    role: str
    room: str


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    id TEXT PRIMARY KEY, room TEXT NOT NULL, owner TEXT NOT NULL, title TEXT NOT NULL,
    created_at REAL NOT NULL, last_audio REAL NOT NULL, started_at REAL, ended_at REAL,
    state TEXT NOT NULL DEFAULT 'AGUARDANDO', processing TEXT NOT NULL DEFAULT 'AGUARDANDO',
    next_seq INTEGER NOT NULL DEFAULT 0, loud INTEGER NOT NULL DEFAULT 0,
    quiet INTEGER NOT NULL DEFAULT 0, recorded INTEGER NOT NULL DEFAULT 0,
    gap INTEGER NOT NULL DEFAULT 0, presence_verified INTEGER, level REAL,
    close_reason TEXT, error TEXT, transcript TEXT, minutes TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_per_room ON captures(room) WHERE ended_at IS NULL;
CREATE TABLE IF NOT EXISTS audio (
    capture_id TEXT NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, digest TEXT NOT NULL, pcm BLOB,
    PRIMARY KEY(capture_id, seq)
);
CREATE TABLE IF NOT EXISTS presence_events (
    device TEXT NOT NULL, event_id TEXT NOT NULL, room TEXT NOT NULL,
    observed_at REAL NOT NULL, people INTEGER,
    PRIMARY KEY(device, event_id)
);
CREATE INDEX IF NOT EXISTS latest_presence ON presence_events(room, observed_at DESC);
"""


class Database:
    def __init__(self, path, policy=Policy(), clock=time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.policy = policy
        self.clock = clock
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _get(db, capture_id, device=None, owner_only=False):
        row = db.execute("SELECT * FROM captures WHERE id=?", (capture_id,)).fetchone()
        if row is None or (device and row["room"] != device.room):
            raise ApiError(404, "Reunião não encontrada nesta sala.")
        if owner_only and row["owner"] != device.id:
            raise ApiError(403, "A captura pertence a outro dispositivo.")
        return row

    @staticmethod
    def _presence(db, room, now, ttl):
        row = db.execute(
            "SELECT observed_at, people FROM presence_events WHERE room=? "
            "ORDER BY observed_at DESC LIMIT 1", (room,),
        ).fetchone()
        if row is None or now - row["observed_at"] > ttl or row["people"] is None:
            return {"people": None, "observedAt": None}
        return {"people": row["people"], "observedAt": row["observed_at"]}

    @staticmethod
    def _result(row, detail=False):
        result = {
            "id": row["id"], "room": row["room"], "title": row["title"],
            "state": row["state"], "processing": row["processing"],
            "startedAt": row["started_at"], "endedAt": row["ended_at"],
            "presenceVerified": None if row["presence_verified"] is None else bool(row["presence_verified"]),
            "quality": "INCOMPLETA" if row["gap"] else "COMPLETA",
            "reason": row["close_reason"], "nextSequence": row["next_seq"],
            "audioSeconds": row["recorded"], "levelDbfs": row["level"], "error": row["error"],
        }
        if detail:
            result["transcript"] = json.loads(row["transcript"]) if row["transcript"] else []
            result["minutes"] = json.loads(row["minutes"]) if row["minutes"] else None
        return result

    @staticmethod
    def _close(db, row, now, reason):
        processing = "PENDENTE" if row["started_at"] is not None else "DESCARTADA"
        db.execute(
            "UPDATE captures SET state='FECHADA', processing=?, ended_at=?, close_reason=? WHERE id=?",
            (processing, now, reason, row["id"]),
        )
        if processing == "DESCARTADA":
            db.execute("UPDATE audio SET pcm=NULL WHERE capture_id=?", (row["id"],))

    def _advance(self, db, row, now):
        elapsed = now - row["last_audio"]
        state = row["state"]
        if state == "AGUARDANDO" and elapsed >= self.policy.waiting_timeout:
            self._close(db, row, now, "captura_inativa")
        elif state in ("ABERTA", "ENCERRANDO", "SUSPENSA"):
            if elapsed >= self.policy.audio_timeout + self.policy.suspension_timeout:
                db.execute("UPDATE captures SET gap=1 WHERE id=?", (row["id"],))
                self._close(db, row, now, "audio_indisponivel")
            elif elapsed >= self.policy.audio_timeout and state != "SUSPENSA":
                db.execute("UPDATE captures SET state='SUSPENSA', gap=1 WHERE id=?", (row["id"],))
        return self._get(db, row["id"])

    def sweep(self):
        now = self.clock()
        with self.connection(write=True) as db:
            for row in db.execute("SELECT * FROM captures WHERE ended_at IS NULL").fetchall():
                self._advance(db, row, now)
            db.execute("DELETE FROM presence_events WHERE observed_at < ?", (now - 3600,))

    def status(self, device):
        now = self.clock()
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT * FROM captures WHERE room=? AND ended_at IS NULL", (device.room,),
            ).fetchone()
            row = self._advance(db, row, now) if row else None
            return {
                "room": device.room, "device": device.id, "role": device.role,
                "presence": self._presence(db, device.room, now, self.policy.presence_ttl),
                "capture": self._result(row) if row and row["ended_at"] is None and device.role == "microphone" else None,
            }

    def presence(self, device, event_id, observed_at, people):
        now = self.clock()
        with self.connection(write=True) as db:
            old = db.execute(
                "SELECT observed_at, people FROM presence_events WHERE device=? AND event_id=?",
                (device.id, event_id),
            ).fetchone()
            if old is not None:
                if (observed_at, people) != (old["observed_at"], old["people"]):
                    raise ApiError(409, "Identificador de presença já usado com outros dados.")
                return {"ack": "duplicado"}
            current = db.execute(
                "SELECT max(observed_at) FROM presence_events WHERE room=?", (device.room,),
            ).fetchone()[0]
            if observed_at > now + 5:
                raise ApiError(400, "Relógio do produtor de presença está adiantado.")
            if observed_at < now - self.policy.presence_ttl or (current is not None and observed_at <= current):
                return {"ack": "atrasado"}
            db.execute(
                "INSERT INTO presence_events VALUES (?, ?, ?, ?, ?)",
                (device.id, event_id, device.room, observed_at, people),
            )
            return {"ack": "aplicado"}

    def start(self, device, capture_id, title):
        now = self.clock()
        with self.connection(write=True) as db:
            existing = db.execute("SELECT id FROM captures WHERE id=?", (capture_id,)).fetchone()
            if existing:
                row = self._get(db, capture_id, device, owner_only=True)
                if row["title"] != title:
                    raise ApiError(409, "Este identificador já foi usado em outra captura.")
                return 200, self._result(self._advance(db, row, now))
            active = db.execute(
                "SELECT * FROM captures WHERE room=? AND ended_at IS NULL", (device.room,),
            ).fetchone()
            if active and self._advance(db, active, now)["ended_at"] is None:
                raise ApiError(409, "A sala já tem uma captura em andamento.")
            db.execute(
                "INSERT INTO captures (id, room, owner, title, created_at, last_audio) VALUES (?, ?, ?, ?, ?, ?)",
                (capture_id, device.room, device.id, title, now, now),
            )
            return 201, self._result(self._get(db, capture_id))

    def capture(self, device, capture_id):
        with self.connection(write=True) as db:
            row = self._get(db, capture_id, device)
            return self._result(self._advance(db, row, self.clock()), detail=True)

    def meetings(self, device):
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM captures WHERE room=? AND started_at IS NOT NULL "
                "ORDER BY started_at DESC, id DESC LIMIT 50", (device.room,),
            )
            return {"meetings": [self._result(row) for row in rows]}

    def add_audio(self, device, capture_id, sequence, pcm, captured_at):
        now = self.clock()
        digest = hashlib.sha256(pcm).hexdigest()
        level = audio_level(pcm)
        with self.connection(write=True) as db:
            row = self._advance(db, self._get(db, capture_id, device, owner_only=True), now)
            if sequence < row["next_seq"]:
                previous = db.execute(
                    "SELECT digest FROM audio WHERE capture_id=? AND seq=?", (capture_id, sequence),
                ).fetchone()
                if previous is None or previous["digest"] != digest:
                    raise ApiError(409, "Sequência já usada com outro áudio.")
                return {**self._result(row), "ack": "duplicado"}
            if row["ended_at"] is not None:
                return {**self._result(row), "ack": "encerrado"}
            if sequence != row["next_seq"]:
                raise ApiError(409, f"Sequência fora de ordem. Esperada: {row['next_seq']}.")
            if captured_at > now + 5:
                raise ApiError(400, "O relógio do celular está adiantado.")
            if captured_at < now - self.policy.audio_timeout:
                return self._late_audio(db, row, sequence, pcm, digest, now)
            return self._live_audio(db, row, sequence, pcm, digest, level, now)

    def _late_audio(self, db, row, sequence, pcm, digest, now):
        has_meeting = row["started_at"] is not None
        db.execute(
            "INSERT INTO audio VALUES (?, ?, ?, ?)",
            (row["id"], sequence, digest, pcm if has_meeting else None),
        )
        db.execute(
            "UPDATE captures SET next_seq=next_seq+1, recorded=recorded+?, "
            "gap=max(gap, ?), loud=0 WHERE id=?", (int(has_meeting), int(has_meeting), row["id"]),
        )
        if not has_meeting:
            db.execute("UPDATE audio SET pcm=NULL WHERE capture_id=? AND pcm IS NOT NULL", (row["id"],))
        updated = self._get(db, row["id"])
        if updated["recorded"] >= self.policy.max_audio_seconds:
            self._close(db, updated, now, "limite_de_duracao")
        return {**self._result(self._get(db, row["id"])), "ack": "arquivado" if has_meeting else "atrasado"}

    def _live_audio(self, db, row, sequence, pcm, digest, level, now):
        capture_id = row["id"]
        state = row["state"]
        started_at = row["started_at"]
        verified = row["presence_verified"]
        loud, quiet, recorded = row["loud"], row["quiet"], row["recorded"]
        speaking = level >= self.policy.voice_dbfs
        keep = True
        if state == "AGUARDANDO":
            if now - row["last_audio"] >= self.policy.audio_timeout:
                db.execute("UPDATE audio SET pcm=NULL WHERE capture_id=? AND pcm IS NOT NULL", (capture_id,))
                loud = 0
            people = self._presence(db, row["room"], now, self.policy.presence_ttl)["people"]
            keep = speaking and people != 0
            loud = loud + 1 if keep else 0
            if not keep:
                db.execute("UPDATE audio SET pcm=NULL WHERE capture_id=? AND pcm IS NOT NULL", (capture_id,))
            if loud >= self.policy.open_seconds:
                state, started_at, recorded = "ABERTA", now - loud, loud
                verified = int(people is not None)
        else:
            quiet = 0 if speaking else quiet + 1
            state = "ABERTA" if speaking else "ENCERRANDO"
            recorded += 1
        db.execute("INSERT INTO audio VALUES (?, ?, ?, ?)", (capture_id, sequence, digest, pcm if keep else None))
        db.execute(
            "UPDATE captures SET state=?, started_at=?, presence_verified=?, loud=?, quiet=?, "
            "recorded=?, last_audio=?, next_seq=?, level=? WHERE id=?",
            (state, started_at, verified, loud, quiet, recorded, now, sequence + 1, level, capture_id),
        )
        updated = self._get(db, capture_id)
        if quiet >= self.policy.silence_seconds:
            self._close(db, updated, now, "silencio")
        elif recorded >= self.policy.max_audio_seconds:
            self._close(db, updated, now, "limite_de_duracao")
        return {**self._result(self._get(db, capture_id)), "ack": "aplicado"}

    def finish(self, device, capture_id):
        with self.connection(write=True) as db:
            now = self.clock()
            row = self._advance(db, self._get(db, capture_id, device, owner_only=True), now)
            if row["ended_at"] is None:
                self._close(db, row, now, "encerramento_manual")
            return self._result(self._get(db, capture_id))

    def retry(self, device, capture_id):
        with self.connection(write=True) as db:
            row = self._get(db, capture_id, device, owner_only=True)
            if row["processing"] == "FALHOU":
                db.execute("UPDATE captures SET processing='PENDENTE', error=NULL WHERE id=?", (capture_id,))
            elif row["processing"] not in ("PENDENTE", "TRANSCREVENDO", "GERANDO_ATA", "PRONTA"):
                raise ApiError(409, "A captura não está pronta para processar.")
            return self._result(self._get(db, capture_id))

    def recover_jobs(self):
        with self.connection(write=True) as db:
            db.execute(
                "UPDATE captures SET processing='PENDENTE' WHERE processing IN ('TRANSCREVENDO','GERANDO_ATA')"
            )

    def claim_job(self):
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT id, transcript FROM captures WHERE processing='PENDENTE' ORDER BY ended_at LIMIT 1"
            ).fetchone()
            if row:
                state = "GERANDO_ATA" if row["transcript"] is not None else "TRANSCREVENDO"
                db.execute("UPDATE captures SET processing=? WHERE id=?", (state, row["id"]))
            return row

    def write_wave(self, capture_id, path):
        with self.connection() as db, wave.open(str(path), "wb") as wav:
            wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            for row in db.execute(
                "SELECT pcm FROM audio WHERE capture_id=? AND pcm IS NOT NULL ORDER BY seq", (capture_id,),
            ):
                wav.writeframesraw(row["pcm"])

    def save_transcript(self, capture_id, segments):
        with self.connection(write=True) as db:
            db.execute(
                "UPDATE captures SET transcript=?, processing='GERANDO_ATA' WHERE id=?",
                (json.dumps(segments, ensure_ascii=False), capture_id),
            )
            db.execute("UPDATE audio SET pcm=NULL WHERE capture_id=?", (capture_id,))

    def save_minutes(self, capture_id, minutes):
        with self.connection(write=True) as db:
            db.execute(
                "UPDATE captures SET minutes=?, processing='PRONTA', error=NULL WHERE id=?",
                (json.dumps(minutes, ensure_ascii=False), capture_id),
            )

    def fail_job(self, capture_id, message):
        with self.connection(write=True) as db:
            db.execute("UPDATE captures SET processing='FALHOU', error=? WHERE id=?", (message, capture_id))


def audio_level(pcm):
    samples = array("h")
    samples.frombytes(pcm)
    if byteorder == "big":
        samples.byteswap()
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    return round(max(-90.0, 20 * math.log10(rms / 32768)), 1) if rms else -90.0


class MeetingProcessor:
    def __init__(self, db, transcribe, summarize, log=print):
        self.db, self.transcribe, self.summarize, self.log = db, transcribe, summarize, log

    def run_once(self):
        job = self.db.claim_job()
        if job is None:
            return False
        capture_id = job["id"]
        stage = "transcrição"
        try:
            if job["transcript"] is None:
                with tempfile.TemporaryDirectory(prefix="towerai-") as folder:
                    wav = Path(folder) / "audio.wav"
                    self.db.write_wave(capture_id, wav)
                    segments = self.transcribe(wav)
                self.db.save_transcript(capture_id, segments)
            else:
                segments = json.loads(job["transcript"])
            stage = "geração da ata"
            minutes = self.summarize(segments) if segments else {
                "summary": "Nenhuma fala inteligível foi identificada.", "decisions": [], "action_items": [],
            }
            self.db.save_minutes(capture_id, minutes)
        except Exception as error:
            self.log(f"{capture_id}: falha na {stage} ({type(error).__name__}): {error}")
            self.db.fail_job(capture_id, f"Falha na {stage} local. Verifique o servidor e tente novamente.")
        return True


def as_time(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ApiError(400, "Horário de captura inválido.")
    return value


class Api:
    def __init__(self, db, devices, on_activity=None):
        self.db, self.devices = db, devices
        self.on_activity = on_activity

    def authenticate(self, header):
        scheme, _, token = (header or "").partition(" ")
        device = self.devices.get(hashlib.sha256(token.encode()).hexdigest()) if scheme == "Bearer" else None
        if device is None:
            raise ApiError(401, "Credencial inválida ou ausente.")
        return device

    @staticmethod
    def require(device, role):
        if device.role != role:
            raise ApiError(403, "O dispositivo não tem permissão para esta operação.")

    def dispatch(self, request):
        device = self.authenticate(request.headers.get("Authorization"))
        if self.on_activity is not None:
            self.on_activity(device)
        method = request.command
        path = urlsplit(request.path).path
        parts = path.strip("/").split("/")
        if path == "/v1/status" and method == "GET":
            return 200, self.db.status(device)
        if path == "/v1/presence" and method == "POST":
            self.require(device, "presence")
            data = request.json()
            event_id, people = data.get("eventId"), data.get("people")
            if not isinstance(event_id, str) or not IDENTIFIER.fullmatch(event_id):
                raise ApiError(400, "Identificador de presença inválido.")
            if "people" not in data or (people is not None and (type(people) is not int or not 0 <= people <= 50)):
                raise ApiError(400, "Estimativa de presença inválida.")
            return 200, self.db.presence(device, event_id, as_time(data.get("observedAt")), people)
        if path == "/v1/meetings" and method == "GET":
            self.require(device, "microphone")
            return 200, self.db.meetings(device)
        if len(parts) >= 3 and parts[:2] == ["v1", "captures"] and IDENTIFIER.fullmatch(parts[2]):
            self.require(device, "microphone")
            capture_id = parts[2]
            if len(parts) == 3 and method == "GET":
                return 200, self.db.capture(device, capture_id)
            if len(parts) == 3 and method == "PUT":
                title = request.json().get("title")
                if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
                    raise ApiError(400, "Informe um título de 1 a 100 caracteres.")
                return self.db.start(device, capture_id, title.strip())
            if len(parts) == 4 and method == "PUT":
                if parts[3] == "finish":
                    return 200, self.db.finish(device, capture_id)
                if parts[3] == "retry":
                    return 202, self.db.retry(device, capture_id)
            if len(parts) == 5 and parts[3] == "chunks" and method == "PUT":
                if not re.fullmatch(r"[0-9]{1,7}", parts[4]):
                    raise ApiError(400, "Sequência de áudio inválida.")
                if request.headers.get_content_type() != "application/octet-stream":
                    raise ApiError(415, "Use application/octet-stream para áudio PCM16.")
                pcm = request.body(CHUNK_BYTES)
                if len(pcm) != CHUNK_BYTES:
                    raise ApiError(400, "Envie 32000 bytes de PCM16 mono a 16000 Hz por bloco.")
                try:
                    captured_at = float(request.headers["X-Captured-At"])
                except (ValueError, TypeError):
                    raise ApiError(400, "Informe X-Captured-At em segundos Unix.") from None
                return 200, self.db.add_audio(device, capture_id, int(parts[4]), pcm, as_time(captured_at))
        raise ApiError(404, "Operação não encontrada.")


class Handler(BaseHTTPRequestHandler):
    server_version = "TowerAI/4"

    def setup(self):
        self.request.settimeout(10)
        super().setup()

    def body(self, maximum):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "Comprimento inválido.") from None
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(400, "Informe o tamanho do corpo em Content-Length.")
        if not 0 <= length <= maximum:
            raise ApiError(413, "Corpo acima do limite.")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ApiError(400, "Corpo incompleto.")
        return data

    def json(self):
        if self.headers.get_content_type() != "application/json":
            raise ApiError(415, "Use application/json.")
        try:
            value = json.loads(self.body(4096))
        except (ValueError, UnicodeError):
            raise ApiError(400, "JSON inválido.") from None
        if not isinstance(value, dict):
            raise ApiError(400, "Envie um objeto JSON.")
        return value

    def respond(self):
        try:
            status, body = self.server.api.dispatch(self)
        except ApiError as error:
            status, body = error.status, {"error": str(error)}
        except TimeoutError:
            status, body = 408, {"error": "Tempo esgotado durante o envio."}
        except Exception as error:
            self.log_error("%s", type(error).__name__)
            status, body = 500, {"error": "O servidor não concluiu a operação."}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            pass

    do_GET = do_POST = do_PUT = respond

    def log_message(self, format, *args):
        if sys.stderr is None:
            logging.getLogger(__name__).info("%s %s", self.address_string(), format % args)
        else:
            super().log_message(format, *args)

    def log_request(self, code="-", size="-"):
        if isinstance(code, int) and code >= 400:
            self.log_message("%s %s", self.command, code)


class Server(ThreadingHTTPServer):
    def __init__(self, address, api, context=None):
        self.api = api
        self.context = context
        super().__init__(address, Handler)

    def process_request_thread(self, request, client_address):
        if self.context is not None:
            request.settimeout(5)
            try:
                request = self.context.wrap_socket(request, server_side=True)
            except OSError:
                request.close()
                return
        super().process_request_thread(request, client_address)

"""Painel do TowerAI Servidor."""

import logging
import multiprocessing
import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from main import PORT, devices_file
from desktop_runtime import DATA_DIR, CameraReading, Configuration, DesktopServer, camera_devices, download_models, validate_name
from meeting_pdf import meeting_date, save_meeting_pdf
from towerai import Database


WINDOWS = sys.platform == "win32"
if WINDOWS:
    from windows_network import allow_firewall, firewall_allowed, list_networks
else:
    from linux_desktop import list_networks, open_folder, panel_lock


BACKGROUND = "#F5F2F9"
SURFACE = "#FFFFFF"
INK = "#221C31"
MUTED = "#625970"
ACCENT = "#6650A4"
GREEN = "#246347"
AMBER = "#87540F"
RED = "#A12D4C"
LINE = "#DDD5E7"
PREVIEW = "#211B2B"
LABELS_CAPTURE = {
    "AGUARDANDO": "Aguardando contexto da reunião", "ABERTA": "Gravando reunião",
    "ENCERRANDO": "Gravando · silêncio detectado", "SUSPENSA": "Captura suspensa · áudio interrompido",
}
LABELS_JOB = {
    "PENDENTE": "Na fila de processamento", "TRANSCREVENDO": "Transcrevendo áudio",
    "GERANDO_ATA": "Gerando ata", "PRONTA": "Ata pronta", "FALHOU": "Falha no processamento",
}
LOG = logging.getLogger(__name__)


def styles(root):
    root.configure(bg=BACKGROUND)
    root.option_add("*Font", "{Segoe UI} 10")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TFrame", background=BACKGROUND)
    style.configure("Panel.TFrame", background=SURFACE)
    style.configure("TLabel", background=SURFACE, foreground=INK, font=("Segoe UI", 10))
    style.configure("Heading.TLabel", font=("Segoe UI Semibold", 15))
    style.configure("Field.TLabel", font=("Segoe UI Semibold", 10))
    style.configure("Metric.TLabel", font=("Segoe UI Semibold", 12))
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("TEntry", padding=(7, 6), fieldbackground=SURFACE, foreground=INK)
    style.configure("TCombobox", padding=(7, 5), foreground=INK, arrowsize=14)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE)], foreground=[("readonly", INK)],
              selectbackground=[("readonly", SURFACE)], selectforeground=[("readonly", INK)])
    style.configure("TButton", padding=(12, 7), background=SURFACE, foreground=INK, bordercolor=LINE,
                    focuscolor=ACCENT, lightcolor=SURFACE, darkcolor=SURFACE)
    style.map("TButton", background=[("pressed", "#E7DFF3"), ("active", "#F1EDF7")],
              foreground=[("disabled", "#81788D")], bordercolor=[("focus", ACCENT)])
    style.configure("Accent.TButton", background=ACCENT, foreground=SURFACE, bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT, focuscolor=SURFACE)
    style.map("Accent.TButton", background=[("disabled", "#E4DDED"), ("pressed", "#4D3888"), ("active", "#594293")],
              foreground=[("disabled", MUTED)])
    style.configure("TNotebook", background=BACKGROUND, bordercolor=LINE, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", padding=(15, 9), background=BACKGROUND, foreground=MUTED)
    style.map("TNotebook.Tab", background=[("selected", SURFACE), ("active", "#EAE4F3")],
              foreground=[("selected", ACCENT)])
    style.configure("TSeparator", background=LINE)
    style.configure("History.Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=INK, rowheight=30)
    style.configure("History.Treeview.Heading", background=BACKGROUND, foreground=INK, padding=(6, 6))
    style.map("History.Treeview", background=[("selected", "#E7DFF3")], foreground=[("selected", INK)])


class App:
    def __init__(self, root, config, prepare_models=False):
        self.root = root
        self.config = config
        self.server = DesktopServer(config)
        self.tasks = queue.SimpleQueue()
        self.busy = False
        self.model_process = None
        self.model_messages = None
        self.model_result = None
        self.closing = False
        self.networks = {}
        self.cameras = {}
        self.firewall_network = None
        self.firewall_open = False
        self.preview_data = None
        self.photo = None
        self.buttons = []
        self.history_dirty = True
        self.history_rows = {}
        root.title("TowerAI Servidor")
        root.geometry("1040x715")
        root.minsize(940, 680)
        styles(root)
        root.protocol("WM_DELETE_WINDOW", self.close)

        content = ttk.Frame(root, padding=(24, 20, 24, 12))
        content.pack(fill="both", expand=True)
        header = ttk.Frame(content)
        header.pack(fill="x", pady=(0, 15))
        tk.Label(header, text="TowerAI Servidor", bg=BACKGROUND, fg=INK,
                 font=("Segoe UI Semibold", 23), padx=0).pack(side="left")
        self.power_button = self.button(header, "Iniciar servidor", self.toggle_server, accent=True)
        self.power_button.pack(side="right", pady=4)
        self.power_label = tk.Label(header, bg=BACKGROUND, fg=MUTED, font=("Segoe UI Semibold", 10))
        self.power_label.pack(side="right", padx=14)
        self.model_header = tk.Label(header, bg=BACKGROUND, fg=ACCENT, padx=0)
        self.model_header.pack(side="right", padx=(10, 0))

        self.tabs = ttk.Notebook(content)
        self.tabs.pack(fill="both", expand=True)
        self.tabs.enable_traversal()
        self.monitor_tab = self.tab("Painel")
        self.network_tab = self.tab("Rede e microfone")
        self.camera_tab = self.tab("Webcam")
        self.models_tab = self.tab("Modelos")
        self.history_tab = self.tab("Histórico")
        self.build_monitor()
        self.build_network()
        self.build_camera()
        self.build_models()
        self.build_history()
        self.tabs.bind("<<NotebookTabChanged>>", self.history_tab_changed)

        footer = ttk.Frame(content)
        footer.pack(fill="x", pady=(10, 0))
        self.notice = tk.Label(footer, bg=BACKGROUND, fg=MUTED, anchor="w", justify="left", wraplength=700)
        self.notice.pack(side="left", fill="x", expand=True)
        ttk.Button(footer, text="Abrir pasta de dados", command=self.open_data_folder).pack(side="right")
        self.refresh_configuration()
        if not self.config.settings.address:
            self.tabs.select(self.network_tab)
        self.refresh_devices()
        if prepare_models and not self.config.models_ready:
            self.start_model_download()
        self.tick()

    def tab(self, title):
        frame = ttk.Frame(self.tabs, style="Panel.TFrame", padding=(22, 19))
        self.tabs.add(frame, text=title)
        return frame

    def button(self, parent, title, action, *, accent=False):
        widget = ttk.Button(parent, text=title, command=action, style="Accent.TButton" if accent else "TButton")
        self.buttons.append(widget)
        return widget

    def line(self, parent, text, *, style="Muted.TLabel", wrap=820, gap=(0, 7)):
        widget = ttk.Label(parent, text=text, style=style, wraplength=wrap, justify="left")
        widget.pack(anchor="w", fill="x", pady=gap)
        return widget

    def build_monitor(self):
        panel = self.monitor_tab
        panel.columnconfigure(0, weight=1, minsize=482)
        panel.columnconfigure(1, weight=1, minsize=320)
        panel.rowconfigure(1, weight=1)
        self.address_label = ttk.Label(panel, foreground=MUTED)
        self.address_label.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 17))
        left = ttk.Frame(panel, style="Panel.TFrame")
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 25))
        self.line(left, "Webcam", style="Heading.TLabel", gap=(0, 9))
        self.preview = tk.Label(left, bg=PREVIEW, fg="#DDD3ED", text="Selecione uma webcam para abrir a prévia",
                                font=("Segoe UI", 11), wraplength=350, borderwidth=0)
        self.preview.pack(fill="both", expand=True)
        self.people_label = self.line(left, "Pessoas detectadas  —", style="Metric.TLabel", gap=(11, 2))
        self.camera_label = self.line(left, "Webcam desligada", wrap=445)

        right = ttk.Frame(panel, style="Panel.TFrame")
        right.grid(row=1, column=1, sticky="nsew")
        self.line(right, "Atividade da sala", style="Heading.TLabel", gap=(0, 11))
        self.activity = {}
        for name in ("Microfone Android", "Reunião", "Processamento da ata"):
            ttk.Separator(right).pack(fill="x", pady=(0, 10))
            self.line(right, name, style="Field.TLabel", gap=(0, 3))
            value = self.line(right, "—", style="Metric.TLabel", wrap=320, gap=(0, 2))
            detail = self.line(right, "", wrap=320, gap=(0, 11))
            self.activity[name] = (value, detail)

    def build_network(self):
        panel = self.network_tab
        self.line(panel, "Conexão do Android", style="Heading.TLabel")
        self.line(panel, "Escolha a rede compartilhada com o celular e gere o certificado para esse endereço.")
        network_row = ttk.Frame(panel, style="Panel.TFrame")
        network_row.pack(fill="x", pady=(2, 7))
        self.network_choice = ttk.Combobox(network_row, state="readonly", width=35)
        self.network_choice.pack(side="left")
        self.network_choice.bind("<<ComboboxSelected>>", self.network_changed)
        self.button(network_row, "Atualizar lista", self.refresh_devices).pack(side="left", padx=(8, 0))
        self.certificate_button = self.button(network_row, "Gerar certificado", self.generate_certificate, accent=True)
        self.certificate_button.pack(side="left", padx=(8, 0))
        firewall_label = "Autorizar porta 8443" if WINDOWS else "Configurar firewall"
        self.firewall_button = self.button(network_row, firewall_label, self.request_firewall)
        self.firewall_button.pack(side="left", padx=(8, 0))
        self.network_info = self.line(panel, "Verificando interfaces de rede…")
        export_row = ttk.Frame(panel, style="Panel.TFrame")
        export_row.pack(fill="x", pady=(2, 5))
        self.export_button = self.button(export_row, "Exportar ca.pem…", self.export_ca)
        self.export_button.pack(side="left")
        self.copy_url_button = self.button(export_row, "Copiar endereço", self.copy_address)
        self.copy_url_button.pack(side="left", padx=(8, 0))
        self.fingerprint_label = self.line(panel, "", wrap=800)
        ttk.Separator(panel).pack(fill="x", pady=(6, 12))
        self.line(panel, "Microfone", style="Heading.TLabel")
        self.line(panel, "No Android, importe o ca.pem, cole o endereço HTTPS e informe a credencial desta sala.")
        form = ttk.Frame(panel, style="Panel.TFrame")
        form.pack(fill="x", pady=(2, 9))
        ttk.Label(form, text="Sala", style="Field.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(form, text="Identificador do microfone", style="Field.TLabel").grid(row=0, column=2, sticky="w", padx=(16, 4))
        self.room_input = ttk.Entry(form, width=16)
        self.room_input.insert(0, self.config.settings.room)
        self.room_input.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.room_button = self.button(form, "Salvar sala", self.save_room)
        self.room_button.grid(row=1, column=1, padx=(7, 0), pady=(4, 0))
        self.microphone_choice = ttk.Combobox(form, width=19)
        self.microphone_choice.grid(row=1, column=2, padx=(16, 4), pady=(4, 0))
        self.microphone_choice.bind("<<ComboboxSelected>>", lambda event: self.clear_token())
        self.microphone_choice.bind("<KeyPress>", lambda event: self.clear_token())
        self.button(form, "Gerar credencial", self.provision, accent=True).grid(row=1, column=3, padx=(5, 0), pady=(4, 0))
        self.button(form, "Revogar", self.revoke).grid(row=1, column=4, padx=(7, 0), pady=(4, 0))
        self.token = tk.StringVar()
        credentials = ttk.Frame(panel, style="Panel.TFrame")
        credentials.pack(fill="x", pady=(1, 5))
        self.token_field = ttk.Entry(credentials, textvariable=self.token, state="readonly", show="•", width=42)
        self.token_field.pack(side="left")
        self.reveal_button = self.button(credentials, "Mostrar", self.toggle_token)
        self.reveal_button.pack(side="left", padx=(7, 0))
        self.copy_token_button = self.button(credentials, "Copiar credencial", lambda: self.copy(self.token.get(), "Credencial copiada."))
        self.copy_token_button.pack(side="left", padx=(7, 0))
        self.save_token_button = self.button(credentials, "Salvar credencial…", self.export_token)
        self.save_token_button.pack(side="left", padx=(7, 0))
        self.line(panel, "A credencial fica disponível nesta tela ao ser gerada. Transfira-a por um canal de confiança.")
        self.import_button = self.button(panel, "Importar instalação anterior…", self.import_installation)
        self.import_button.pack(anchor="w", pady=(6, 0))

    def build_camera(self):
        panel = self.camera_tab
        self.line(panel, "Webcam da sala", style="Heading.TLabel")
        self.line(panel, "Escolha a webcam conectada a este PC. Enquadre o tronco e os ombros das pessoas sentadas.")
        row = ttk.Frame(panel, style="Panel.TFrame")
        row.pack(fill="x", pady=(5, 12))
        self.camera_choice = ttk.Combobox(row, state="readonly", width=48)
        self.camera_choice.pack(side="left")
        self.camera_choice.bind("<<ComboboxSelected>>", self.camera_changed)
        self.button(row, "Atualizar lista", self.refresh_devices).pack(side="left", padx=(8, 0))
        self.button(row, "Testar webcam", self.test_camera, accent=True).pack(side="left", padx=(8, 0))
        self.button(row, "Desligar webcam", self.disable_camera).pack(side="left", padx=(8, 0))
        self.line(panel, "A prévia e a contagem aparecem no Painel. As imagens são processadas na memória deste computador.")

    def build_models(self):
        panel = self.models_tab
        self.line(panel, "Modelos locais", style="Heading.TLabel")
        self.line(panel, "Prepare os pesos por download ou indique os arquivos que já estão neste PC.")
        ttk.Separator(panel).pack(fill="x", pady=(5, 12))
        for title, name, action in (("Transcrição", "Whisper Large v3 Turbo", self.import_whisper),
                                    ("Geração de ata", "Qwen2.5 1.5B Instruct", self.import_text)):
            self.line(panel, title, style="Field.TLabel", gap=(0, 3))
            self.line(panel, name, style="Metric.TLabel", gap=(0, 2))
            status = self.line(panel, "", wrap=820, gap=(0, 4))
            model_button = self.button(panel, "Indicar pasta Whisper…" if title == "Transcrição" else "Indicar arquivo GGUF…", action)
            model_button.pack(anchor="w", pady=(0, 13))
            if title == "Transcrição":
                self.whisper_status, self.whisper_button = status, model_button
            else:
                self.text_status, self.text_button = status, model_button
        ttk.Separator(panel).pack(fill="x", pady=(2, 13))
        download_row = ttk.Frame(panel, style="Panel.TFrame")
        download_row.pack(anchor="w")
        self.download_button = self.button(download_row, "Baixar modelos restantes", self.start_model_download, accent=True)
        self.download_button.pack(side="left")
        self.cancel_download_button = ttk.Button(download_row, text="Interromper download", command=self.cancel_model_download)
        self.cancel_download_button.pack(side="left", padx=(8, 0))
        self.download_status = self.line(panel, "", gap=(8, 0))
        self.line(panel, "O download inicial usa internet. Os pesos padrão somam cerca de 2,8 GB e a inferência roda em CPU.", gap=(11, 0))

    def set_notice(self, text, error=False):
        self.notice.configure(text=text, fg=RED if error else MUTED)

    def build_history(self):
        panel = self.history_tab
        self.line(panel, "Histórico de reuniões", style="Heading.TLabel")
        self.line(panel, "Selecione uma reunião encerrada para exportar o histórico em PDF, com a gravação WAV quando disponível.")
        table = ttk.Frame(panel, style="Panel.TFrame")
        table.pack(fill="both", expand=True, pady=(8, 10))
        columns = ("title", "date", "room", "processing", "quality")
        self.history_tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse",
                                         style="History.Treeview")
        for key, label, width in zip(
            columns, ("Reunião", "Início", "Sala", "Status", "Captura"), (275, 145, 100, 175, 105)
        ):
            self.history_tree.heading(key, text=label, anchor="w")
            self.history_tree.column(key, width=width, minwidth=85, stretch=key in {"title", "room"})
        scroll = ttk.Scrollbar(table, command=self.history_tree.yview)
        scroll.pack(side="right", fill="y")
        self.history_tree.configure(yscrollcommand=scroll.set)
        self.history_tree.pack(side="left", fill="both", expand=True)
        self.history_tree.bind("<<TreeviewSelect>>", lambda event: self.history_controls())
        self.history_tree.bind("<Double-1>", self.history_double_click)
        self.history_tree.bind("<Return>", lambda event: self.export_history())

        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.pack(fill="x")
        self.history_status = ttk.Label(actions, text="Carregando histórico…", style="Muted.TLabel")
        self.history_status.pack(side="left")
        self.history_export_button = self.button(actions, "Exportar PDF…", self.export_history, accent=True)
        self.history_export_button.pack(side="right")
        self.button(actions, "Atualizar lista", self.refresh_history).pack(side="right", padx=(0, 8))

    def history_tab_changed(self, event):
        if self.tabs.select() == str(self.history_tab):
            self.history_dirty = True

    def history_controls(self):
        enabled = bool(self.history_tree.selection()) and not self.busy
        self.history_export_button.state(["!disabled"] if enabled else ["disabled"])

    def refresh_history(self):
        if self.busy:
            return
        self.history_dirty = False

        def show(rows):
            selection = self.history_tree.selection()
            self.history_tree.delete(*self.history_tree.get_children())
            self.history_rows = {row["id"]: row for row in rows}
            for row in rows:
                date = meeting_date(row["started_at"]).strftime("%d/%m/%Y %H:%M")
                quality = "Incompleta" if row["gap"] else "Completa"
                status = LABELS_JOB.get(row["processing"], "Encerrada")
                self.history_tree.insert("", "end", iid=row["id"], values=(row["title"], date, row["room"], status, quality))
            if selection and selection[0] in self.history_rows:
                self.history_tree.selection_set(selection[0])
                self.history_tree.see(selection[0])
            message = f"Reuniões encerradas: {len(rows)}" if rows else "Nenhuma reunião encerrada. Atualize a lista após encerrar uma captura."
            self.history_status.configure(text=message)
            self.history_controls()

        def read():
            try:
                return Database.exportable_meetings(self.config.database)
            except Exception:
                LOG.exception("Falha ao consultar o histórico de reuniões")
                return None

        def loaded(rows):
            if rows is None:
                self.history_status.configure(text="Falha ao ler o histórico. Atualize a lista para tentar novamente.")
            else:
                show(rows)

        self.history_status.configure(text="Carregando histórico…")
        self.run_task("Consultando o histórico de reuniões…", read, done=loaded)

    def history_double_click(self, event):
        if self.history_tree.identify_row(event.y) in self.history_tree.selection():
            self.export_history()

    def export_history(self):
        selection = self.history_tree.selection()
        if self.busy or not selection:
            return
        meeting = self.history_rows[selection[0]]
        started = meeting_date(meeting["started_at"])
        destination = filedialog.asksaveasfilename(
            parent=self.root, title="Exportar histórico da reunião", defaultextension=".pdf",
            initialfile=f"reuniao-{started:%Y%m%d-%H%M}-{meeting['id'][:8]}.pdf", filetypes=[("Documento PDF", "*.pdf")],
        )
        if destination:
            self.run_task("Gerando PDF da reunião…", lambda: save_meeting_pdf(self.config.database, meeting["id"], destination),
                          success=f"PDF salvo em {destination}")

    def open_data_folder(self):
        try:
            if WINDOWS:
                os.startfile(self.config.folder)
            else:
                open_folder(self.config.folder)
        except OSError:
            LOG.exception("Falha ao abrir a pasta de dados")
            self.set_notice(f"Abra a pasta pelo gerenciador de arquivos: {self.config.folder}", error=True)

    def run_task(self, message, action, *, done=None, success=""):
        if self.busy:
            return
        self.busy = True
        self.set_notice(message)
        self.controls()

        def work():
            try:
                self.tasks.put(("done", (action(), done, success)))
            except Exception as error:
                LOG.exception("%s", message)
                self.tasks.put(("error", str(error)))

        threading.Thread(target=work, name="operacao", daemon=True).start()

    def report(self, message):
        self.tasks.put(("progress", message))

    def finish_tasks(self):
        while not self.tasks.empty():
            kind, payload = self.tasks.get()
            if kind == "progress":
                self.set_notice(payload)
                continue
            self.busy = False
            if kind == "error":
                self.closing = False
                self.set_notice(payload, error=True)
            else:
                result, done, success = payload
                self.set_notice(success)
                if done:
                    done(result)
            if self.closing:
                self.root.destroy()
                return False
            self.refresh_configuration()
        return True

    def controls(self):
        for button in self.buttons:
            button.state(["disabled"] if self.busy else ["!disabled"])
        self.history_controls()
        self.network_choice.configure(state="disabled" if self.busy or self.server.running else "readonly")
        self.camera_choice.configure(state="disabled" if self.busy else "readonly")
        self.cancel_download_button.state(["!disabled"] if self.model_process is not None else ["disabled"])
        if self.busy:
            return
        if self.server.running:
            for widget in (self.certificate_button, self.room_button, self.whisper_button,
                           self.text_button, self.download_button, self.import_button):
                widget.state(["disabled"])
        if self.model_process is not None:
            for widget in (self.whisper_button, self.text_button, self.download_button):
                widget.state(["disabled"])
        if self.config.models_ready:
            self.download_button.state(["disabled"])
        if not self.config.settings.address:
            self.copy_url_button.state(["disabled"])
        if not (self.config.tls / "ca.pem").is_file():
            self.export_button.state(["disabled"])
        if self.config.local.exists():
            self.import_button.state(["disabled"])
        if not self.token.get():
            for widget in (self.reveal_button, self.copy_token_button, self.save_token_button):
                widget.state(["disabled"])

    def refresh_devices(self):
        def apply(result):
            network_items, camera_items, network_error = result
            selected = self.network_choice.get()
            self.networks = {network.label: network for network in network_items}
            self.network_choice.configure(values=tuple(self.networks))
            matching = next((item.label for item in network_items if item.address == self.config.settings.address), "")
            self.network_choice.set(selected if selected in self.networks else matching or next(iter(self.networks), ""))
            self.cameras = camera_items
            self.camera_choice.configure(values=("Sem webcam", *(label for label, _ in camera_items.values())))
            self.camera_choice.set(camera_items.get(self.config.settings.camera, ("Sem webcam", None))[0])
            self.network_changed()
            if network_error:
                self.set_notice("Falha ao listar redes. Confira a conexão e atualize a lista.", error=True)

        def discover():
            cameras = {key: (f"{camera.name} · câmera {camera.index}", camera) for key, camera in camera_devices().items()}
            try:
                return list_networks(), cameras, False
            except (RuntimeError, OSError):
                LOG.exception("Falha ao enumerar redes")
                return [], cameras, True

        self.run_task("Procurando redes e webcams…", discover, done=apply)

    def refresh_configuration(self):
        settings = self.config.settings
        self.address_label.configure(text=f"{self.address()}  ·  Sala {settings.room}" if settings.address else f"Sala {settings.room} · endereço pendente")
        microphones = self.config.microphones()
        self.microphone_choice.configure(values=microphones)
        if not self.microphone_choice.get():
            self.microphone_choice.set(microphones[0] if microphones else "android-01")
        self.whisper_status.configure(text=str(self.config.whisper) if self.config.whisper_ready else "Pesos pendentes")
        self.text_status.configure(text=str(self.config.text) if self.config.text.is_file() else "Pesos pendentes")
        try:
            fingerprint = self.config.fingerprint() if (self.config.tls / "ca.pem").is_file() else ""
            self.fingerprint_label.configure(text=f"Impressão digital SHA-256  {fingerprint}" if fingerprint else "")
        except (ValueError, OSError):
            self.fingerprint_label.configure(text="CA inválida. Gere o certificado novamente.")
        self.show_network()
        self.controls()

    def address(self):
        return f"https://{self.config.settings.address}:{PORT}"

    def show_network(self):
        network = self.networks.get(self.network_choice.get())
        if network is None:
            message = "Nenhuma rede disponível. Conecte o PC à rede local e atualize a lista."
        else:
            if not WINDOWS:
                firewall = f"Sub-rede {network.subnet} · conexão local do Android"
            elif network.profile != "Private":
                firewall = "Defina esta rede de confiança como Privada no Windows para autorizar a porta."
            elif self.firewall_network == network and self.firewall_open:
                firewall = "Regra TCP 8443 ativa para a rede privada."
            else:
                firewall = "Autorização TCP 8443 pendente ou ainda não verificada."
            message = f"https://{network.address}:{PORT}  ·  {firewall}"
        self.network_info.configure(text=message)

    def network_changed(self, event=None):
        self.show_network()
        network = self.networks.get(self.network_choice.get())
        if not WINDOWS or network is None or network.profile != "Private":
            return

        def applied(allowed):
            self.firewall_network, self.firewall_open = network, allowed
            self.show_network()

        self.run_task("Verificando o Firewall do Windows…", lambda: firewall_allowed(network), done=applied)

    def generate_certificate(self):
        network = self.networks.get(self.network_choice.get())
        if network is None:
            self.set_notice("Selecione uma interface de rede.", error=True)
            return
        if ((self.config.tls / "ca.pem").is_file() and not self.config.certificate_matches(network.address)
                and not messagebox.askyesno("Trocar certificado", "Uma nova CA será criada. Importe o novo ca.pem no Android. Continuar?", parent=self.root)):
            return
        def configured(_):
            if WINDOWS and network.profile == "Private" and (self.firewall_network != network or not self.firewall_open):
                self.request_firewall()

        self.run_task("Preparando certificado HTTPS…", lambda: self.config.issue_certificate(network.address, network.alias),
                      done=configured, success="Certificado pronto. Exporte o ca.pem para o Android.")

    def request_firewall(self):
        network = self.networks.get(self.network_choice.get())
        if not WINDOWS:
            if network is None:
                self.set_notice("Selecione a interface conectada ao Android.", error=True)
            else:
                messagebox.showinfo("Conexão do Android", f"Se o firewall estiver ativo, permita TCP {PORT} "
                                    f"de {network.subnet} para {network.address} na interface {network.alias}.",
                                    parent=self.root)
            return
        if network is None or network.profile != "Private":
            self.set_notice("Selecione uma rede de confiança configurada como Privada no Windows.", error=True)
            return
        if not messagebox.askyesno("Autorizar conexão do Android",
                                   f"Permitir conexões TCP na porta {PORT} para o TowerAI em {network.alias}, vindas da sub-rede local?\n\nO Windows solicitará permissão de administrador.",
                                   parent=self.root):
            self.set_notice("Autorização pendente. Você pode concedê-la depois.")
            return

        def allowed(_):
            self.firewall_network, self.firewall_open = network, True

        self.run_task("Aguardando a autorização do Windows…", lambda: allow_firewall(network),
                      done=allowed, success="Porta autorizada para a rede escolhida.")

    def export_ca(self):
        destination = filedialog.asksaveasfilename(parent=self.root, initialfile="ca.pem", defaultextension=".pem",
                                                  filetypes=[("Certificado PEM", "*.pem")])
        if destination:
            source = self.config.tls / "ca.pem"
            target = Path(destination).resolve()
            if target.is_relative_to(self.config.folder.resolve()):
                self.set_notice("Escolha uma pasta de transferência fora da pasta de dados do TowerAI.", error=True)
                return
            self.run_task("Exportando certificado…", lambda: shutil.copyfile(source, target),
                          success="ca.pem exportado. Importe o arquivo no Android.")

    def copy(self, value, notice):
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.set_notice(notice)

    def copy_address(self):
        self.copy(self.address(), "Endereço HTTPS copiado.")

    def save_room(self):
        room = self.room_input.get().strip()
        self.clear_token()
        self.run_task("Salvando sala…", lambda: self.config.save(room=validate_name(room)), success="Sala salva.")

    def clear_token(self):
        if self.token.get():
            self.token.set("")
            self.token_field.configure(show="•")
            self.reveal_button.configure(text="Mostrar")
            self.controls()

    def toggle_token(self):
        hiding = bool(self.token_field.cget("show"))
        self.token_field.configure(show="" if hiding else "•")
        self.reveal_button.configure(text="Ocultar" if hiding else "Mostrar")

    def provision(self):
        device = self.microphone_choice.get().strip()
        previous = devices_file(self.config.devices).get(device)
        if previous and not messagebox.askyesno("Substituir credencial",
                                                f"A credencial anterior de {device} deixará de funcionar. Continuar?", parent=self.root):
            return
        self.clear_token()
        self.run_task("Criando credencial…", lambda: self.server.provision(device), done=self.token.set,
                      success="Credencial gerada. Copie ou salve para configurar o Android.")

    def revoke(self):
        device = self.microphone_choice.get().strip()
        if device not in self.config.microphones():
            self.set_notice("Selecione um microfone cadastrado nesta sala.", error=True)
            return
        if messagebox.askyesno("Revogar microfone", f"Revogar o acesso de {device} ao servidor?", parent=self.root):
            self.clear_token()
            self.run_task("Revogando microfone…", lambda: self.server.revoke(device), success="Credencial revogada.")

    def export_token(self):
        destination = filedialog.asksaveasfilename(parent=self.root, initialfile="credencial-microfone.txt",
                                                  defaultextension=".txt", filetypes=[("Arquivo de texto", "*.txt")])
        if destination:
            text = self.token.get() + "\n"
            path = Path(destination).resolve()
            if path.is_relative_to(self.config.folder.resolve()):
                self.set_notice("Escolha uma pasta de transferência fora da pasta de dados do TowerAI.", error=True)
                return
            self.run_task("Salvando credencial…", lambda: path.write_text(text, encoding="utf-8"),
                          success="Credencial salva. Transfira o arquivo de forma privada.")

    def import_installation(self):
        folder = filedialog.askdirectory(parent=self.root, title="Selecione a pasta local do servidor anterior")
        if folder:
            self.run_task("Importando certificados, cadastro e histórico…", lambda: self.config.import_installation(folder),
                          success="Instalação importada. Escolha o endereço de rede e confira o certificado.")

    def camera_selected(self):
        label = self.camera_choice.get()
        return next(((key, camera) for key, (name, camera) in self.cameras.items() if name == label), ("", None))

    def camera_changed(self, event=None):
        key, camera = self.camera_selected()

        def select():
            if self.server.running and camera is not None:
                self.server.camera.start(camera)
            elif camera is None:
                self.server.camera.stop()
            self.config.save(camera=key)

        self.run_task("Salvando seleção da webcam…", select, success="Seleção de webcam salva.")

    def test_camera(self):
        key, camera = self.camera_selected()
        if camera is None:
            self.set_notice("Selecione uma webcam. Atualize a lista se acabou de conectá-la.", error=True)
            return

        def start():
            self.server.camera.start(camera)
            self.config.save(camera=key)

        self.tabs.select(self.monitor_tab)
        self.run_task("Abrindo a prévia da webcam…", start)

    def disable_camera(self):
        self.camera_choice.set("Sem webcam")
        self.camera_changed()

    def import_whisper(self):
        folder = filedialog.askdirectory(parent=self.root, title="Selecione a pasta dos pesos Whisper")
        if folder:
            self.run_task("Verificando arquivos do Whisper…", lambda: self.config.import_models(folder, None),
                          success="Pasta Whisper configurada.")

    def import_text(self):
        file = filedialog.askopenfilename(parent=self.root, title="Selecione um GGUF compatível com geração de ata",
                                          filetypes=[("Modelo GGUF", "*.gguf")])
        if file:
            self.run_task("Verificando arquivo GGUF…", lambda: self.config.import_models(None, file),
                          success="Modelo de ata configurado.")

    def start_model_download(self):
        if self.model_process is not None or self.server.running or self.config.models_ready:
            return
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=download_models, args=(self.config.folder, sender), daemon=True)
        try:
            process.start()
        except OSError as error:
            receiver.close()
            self.download_status.configure(text=f"Falha ao iniciar o download. {error}", foreground=RED)
            return
        finally:
            sender.close()
        self.model_process, self.model_messages = process, receiver
        self.model_result = None
        self.model_header.configure(text="Baixando modelos…")
        self.download_status.configure(text="Preparando download…", foreground=MUTED)
        self.controls()

    def poll_model_download(self):
        process = self.model_process
        if process is None:
            return
        finished = not process.is_alive()
        while True:
            try:
                if not self.model_messages.poll():
                    break
                kind, payload = self.model_messages.recv()
            except (EOFError, OSError):
                break
            if kind == "progress" and self.model_result is None:
                self.download_status.configure(text=payload)
            elif kind == "settings":
                try:
                    self.config.save(**payload)
                except OSError:
                    LOG.exception("Falha ao registrar modelos")
                    self.model_result = ("error", "Não foi possível salvar a configuração dos modelos.")
                else:
                    self.refresh_configuration()
            elif self.model_result is None:
                self.model_result = (kind, payload)
        if not finished:
            return

        process.join()
        process.close()
        self.model_messages.close()
        self.model_process = self.model_messages = None
        kind, message = self.model_result or ("error", "O download foi encerrado. Tente novamente.")
        if kind == "error":
            LOG.error("Falha no download: %s", message)
            message = f"Download pendente. Confira a conexão e tente novamente. {message}"
        self.model_header.configure(text="Download pendente" if kind == "error" else "")
        self.download_status.configure(text=message, foreground=RED if kind == "error" else MUTED)
        self.refresh_configuration()

    def cancel_model_download(self):
        if self.model_process is not None and self.model_process.is_alive():
            self.model_process.terminate()
            self.model_result = ("cancelled", "Download interrompido. Você pode tentar novamente quando quiser.")

    def toggle_server(self):
        if self.server.running:
            self.run_task("Parando o servidor e concluindo operações em andamento…", self.server.stop, success="Servidor parado.")
            return
        settings = self.config.settings
        if not self.config.certificate_matches(settings.address) or not self.config.microphones():
            self.tabs.select(self.network_tab)
            self.set_notice("Configure o endereço, o certificado HTTPS e o microfone antes de iniciar.", error=True)
            return
        if self.model_process is not None or not self.config.models_ready:
            self.tabs.select(self.models_tab)
            if self.model_process is not None:
                self.set_notice("O servidor pode iniciar assim que os modelos estiverem prontos.")
            else:
                self.set_notice("Prepare os modelos de transcrição e de ata antes de iniciar.", error=True)
            return
        network = next((item for item in self.networks.values() if item.address == settings.address and item.alias == settings.interface), None)
        if settings.address != "127.0.0.1" and network is None:
            self.tabs.select(self.network_tab)
            self.set_notice("O endereço certificado não está nesta rede. Atualize a lista e confira o endereço.", error=True)
            return
        if WINDOWS and network is not None and (self.firewall_network != network or not self.firewall_open):
            self.network_choice.set(network.label)
            self.tabs.select(self.network_tab)
            self.show_network()
            if not messagebox.askyesno("Firewall pendente",
                                       "A regra de TCP 8443 ainda está pendente. Use Autorizar porta 8443 para liberar a rede local.\n\nIniciar mesmo assim?",
                                       parent=self.root):
                return
        camera = self.cameras.get(settings.camera, (None, None))[1]

        def start():
            self.server.start(self.report)
            if camera is not None:
                self.server.camera.start(camera)

        self.tabs.select(self.monitor_tab)
        self.run_task("Iniciando servidor…", start, success="Servidor iniciado. Abra o TowerAI no Android para conectar o microfone.")

    def show_activity(self, name, value, detail="", color=INK):
        title, description = self.activity[name]
        title.configure(text=value, foreground=color)
        description.configure(text=detail)

    def tick(self):
        if not self.finish_tasks():
            return
        if self.history_dirty and not self.busy and self.tabs.select() == str(self.history_tab):
            self.refresh_history()
        self.poll_model_download()
        running = self.server.running
        self.power_label.configure(text="Em execução" if running else "Parado", fg=GREEN if running else MUTED)
        self.power_button.configure(text="Parar servidor" if running else "Iniciar servidor")
        reading = self.server.camera.reading
        if reading.updated and time.monotonic() - reading.updated >= 6:
            reading = CameraReading(message="Webcam sem leitura recente")
        people = str(reading.people) if reading.people is not None else "—"
        self.people_label.configure(text=f"Pessoas detectadas  {people}")
        self.camera_label.configure(text=reading.message)
        if reading.image is not self.preview_data:
            self.preview_data = reading.image
            self.photo = tk.PhotoImage(data=reading.image, format="PPM") if reading.image else None
            self.preview.configure(image=self.photo or "", text="" if self.photo else "Sem imagem da webcam")

        status = self.server.dashboard()
        if status is None:
            self.show_activity("Microfone Android", "Aguardando o servidor")
            self.show_activity("Reunião", "Sem captura ativa")
            self.show_activity("Processamento da ata", "Servidor parado")
        else:
            capture, job, waiting, last_seen, audio_timeout, text_ready = status
            self.render_activity(capture, job, waiting, last_seen, audio_timeout, text_ready)
        self.root.after(750, self.tick)

    def render_activity(self, capture, job, waiting, last_seen, audio_timeout, text_ready):
        if capture and capture["level"] is not None and time.time() - capture["last_audio"] < audio_timeout:
            self.show_activity("Microfone Android", "Recebendo áudio", f"Nível {capture['level']:.1f} dBFS", GREEN)
        elif last_seen is None:
            self.show_activity("Microfone Android", "Aguardando conexão", "Abra o TowerAI e toque em Conectar no Android.", AMBER)
        else:
            age = int(time.monotonic() - last_seen)
            detail = f"Último contato há {age} s"
            self.show_activity("Microfone Android", "Conectado · sem áudio" if age < 8 else "Sem comunicação recente",
                               detail, GREEN if age < 8 else AMBER)
        if capture:
            self.show_activity("Reunião", LABELS_CAPTURE.get(capture["state"], capture["state"]),
                               f"{capture['title']} · {capture['recorded']} s de áudio", AMBER if capture["state"] == "SUSPENSA" else INK)
        else:
            self.show_activity("Reunião", "Sem captura ativa", "Ative o microfone no Android para começar.")
        queued = f" · {waiting} na fila" if waiting else ""
        if not text_ready:
            self.show_activity("Processamento da ata", "Modelo de ata indisponível", f"Reinicie o servidor e confira ata.log{queued}.", RED)
        elif job:
            state = job["processing"]
            detail = f"{job['title']}{queued}"
            if job["error"]:
                detail = f"{detail}\n{job['error']}"
            self.show_activity("Processamento da ata", LABELS_JOB.get(state, state), detail,
                               RED if state == "FALHOU" else GREEN if state == "PRONTA" else INK)
        else:
            self.show_activity("Processamento da ata", "Sem atas em processamento")

    def close(self):
        if self.busy:
            self.set_notice("Aguarde a operação atual antes de fechar a janela.")
            return
        if self.model_process is not None:
            if not messagebox.askyesno("Interromper download", "Interromper o download de modelos e fechar o painel?", parent=self.root):
                return
            self.cancel_model_download()
        if self.server.running and not messagebox.askyesno("Parar servidor", "Fechar o painel vai parar o servidor. Continuar?", parent=self.root):
            return
        self.closing = True
        self.run_task("Encerrando o servidor…", self.server.stop)


@contextmanager
def windows_panel_lock():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    mutex = kernel.CreateMutexW(None, False, r"Local\TowerAI.Servidor.Marco4")
    try:
        yield mutex and ctypes.get_last_error() != 183
    finally:
        if mutex:
            kernel.CloseHandle(mutex)


def main():
    root = tk.Tk()
    try:
        with (windows_panel_lock() if WINDOWS else panel_lock(DATA_DIR)) as acquired:
            if not acquired:
                messagebox.showinfo("TowerAI Servidor", "O painel já está aberto.", parent=root)
                root.destroy()
                return
            config = Configuration()
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                                handlers=[RotatingFileHandler(config.folder / "servidor.log", maxBytes=2_000_000,
                                                             backupCount=2, encoding="utf-8")])
            App(root, config, prepare_models="--preparar-modelos" in sys.argv[1:])
            root.mainloop()
    except Exception as error:
        LOG.exception("Falha ao abrir o painel")
        messagebox.showerror("TowerAI Servidor", str(error), parent=root)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()

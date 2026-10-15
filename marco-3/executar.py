"""Ponto de entrada do Marco 3: preparação, diagnóstico e execução assistida.

Começa só com a biblioteca padrão, para poder preparar o próprio .venv.
Todos os caminhos partem deste arquivo, independentemente da pasta do terminal.
Certificados/contas existentes nunca são regenerados para iniciar uma demo.
"""
import argparse
from contextlib import ExitStack
import importlib.metadata
import os
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from mqtt_seguro import preparar_interrupcao

MARCO = Path(__file__).resolve().parent
RAIZ = MARCO.parent
PYTHON = RAIZ / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
COMPOSE = ["docker", "compose", "-f", str(MARCO / "broker/compose.yaml")]
LOCAL = MARCO / "broker/local"
VERSOES = {"paho-mqtt": "2.1.0", "cryptography": "47.0.0"}
IMPRESSAO = threading.Lock()


class Orientacao(Exception):
    """Problema de preparação que deve aparecer sem traceback para o usuário."""


def mostrar(texto=""):
    with IMPRESSAO:
        print(texto, flush=True)


def ambiente():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    # Cada processo usa o perfil da SUA conta. Uma senha global de outro
    # terminal não deve transformar consumidor, painel e sensor na mesma conta.
    for nome in ("MQTT_USUARIO", "MQTT_SENHA", "MQTT_CA", "MQTT_BROKER", "MQTT_PORTA"):
        env.pop(nome, None)
    return env


def rodar(comando, **opcoes):
    return subprocess.run([str(p) for p in comando], cwd=RAIZ, env=ambiente(),
                          check=True, **opcoes)


def verificar_docker():
    if not shutil.which("docker"):
        raise Orientacao("Docker não encontrado. Instale o Docker Desktop e abra-o antes de continuar.")
    try:
        info = rodar(["docker", "info", "--format", "{{.OSType}}"],
                     capture_output=True, text=True, timeout=15)
        rodar(COMPOSE + ["version"], capture_output=True, timeout=10)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise Orientacao("Abra o Docker Desktop, aguarde o motor iniciar e tente novamente. "
                         "Ele precisa estar no modo de containers Linux.") from None
    if info.stdout.strip() != "linux":
        raise Orientacao("No Docker Desktop, mude para containers Linux e tente novamente.")


def preparar_python():
    """Instala apenas quando necessário; a segunda execução pode ser offline."""
    temporarios = RAIZ / ".tmp/marco3"
    temporarios.mkdir(parents=True, exist_ok=True)
    env = ambiente() | {"TEMP": str(temporarios), "TMP": str(temporarios), "TMPDIR": str(temporarios)}
    if not PYTHON.exists():
        mostrar("[PREPARAR] Criando o ambiente Python do projeto (.venv)...")
        subprocess.run([sys.executable, "-m", "venv", str(RAIZ / ".venv")],
                       cwd=RAIZ, env=env, check=True)
    pip = subprocess.run([str(PYTHON), "-m", "pip", "--version"],
                         env=env, capture_output=True, cwd=RAIZ)
    if pip.returncode:
        subprocess.run([str(PYTHON), "-m", "ensurepip", "--upgrade"],
                       cwd=RAIZ, env=env, check=True)
    consulta = "import importlib.metadata as m; " + "; ".join(
        f"assert m.version({nome!r}) == {versao!r}" for nome, versao in VERSOES.items())
    pronto = subprocess.run([str(PYTHON), "-c", consulta], capture_output=True, cwd=RAIZ, env=env)
    if pronto.returncode:
        mostrar("[PREPARAR] Instalando as dependências do Marco 3 (requer internet na primeira vez)...")
        subprocess.run([str(PYTHON), "-m", "pip", "install", "-r", str(MARCO / "requirements.txt")],
                       cwd=RAIZ, env=env, check=True)


def verificar_arquivos():
    obrigatorios = ["ca.pem", "server.p12", "config.xml",
                   "extensions/hivemq-file-rbac-extension/conf/config.xml",
                   "extensions/hivemq-file-rbac-extension/conf/credentials.xml",
                   "consumidor-sala-204.env", "painel-sala-204.env", "mic-esp32-01.env", "mic-injetor.env"]
    faltam = [nome for nome in obrigatorios if not (LOCAL / nome).is_file()]
    if faltam:
        raise Orientacao("Preparação local incompleta: " + ", ".join(faltam)
                         + ". Consulte marco-3/broker/README.md; não apague a CA para tentar corrigir.")


def verificar_tls(host):
    contexto = ssl.create_default_context(cafile=str(LOCAL / "ca.pem"))
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection((host, 8883), timeout=2) as tcp:
        with contexto.wrap_socket(tcp, server_hostname=host) as tls:
            return tls.version()


def iniciar_broker(host, ips=()):
    if not LOCAL.exists():
        mostrar("[PREPARAR] Gerando certificados e contas locais pela primeira vez...")
        comando = [PYTHON, MARCO / "broker/preparar.py"]
        for ip in ips:
            comando += ["--ip", ip]
        rodar(comando)
    else:
        mostrar("[PREPARAR] Reutilizando certificados e contas existentes.")
        if ips:
            mostrar("[PREPARAR] --ip só vale no primeiro provisionamento; os SANs atuais foram preservados. "
                    "Para alterar o certificado, consulte marco-3/broker/README.md.")
    verificar_arquivos()
    rodar(COMPOSE + ["up", "-d"])
    mostrar("[PREPARAR] Aguardando o broker local responder por TLS...")
    limite = time.monotonic() + 40
    while time.monotonic() < limite:
        try:
            versao = verificar_tls(host)
            mostrar(f"[OK] Broker local acessível em {host}:8883 ({versao}, certificado validado).")
            return
        except ssl.SSLCertVerificationError:
            raise Orientacao("O certificado não corresponde ao endereço ou à CA. Confira hostname, SAN e relógio "
                             "no guia marco-3/broker/README.md.") from None
        except (OSError, ssl.SSLError):
            time.sleep(0.5)
    raise Orientacao("O broker não respondeu por TLS em 40 s. Use 'python marco-3/executar.py logs'. "
                     "Se definiu MQTT_BIND_IP na LAN, informe o mesmo endereço com --broker.")


def verificar_conta(conta, host):
    """Confere CONNACK com o perfil individual, sem mostrar senha nem abrir a aplicação."""
    import paho.mqtt.client as mqtt
    from mqtt_seguro import configurar
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="diagnostico-" + uuid4().hex)
    args = SimpleNamespace(config=LOCAL / f"{conta}.env", broker=host, porta=8883)
    configurar(client, args)
    pronto = threading.Event()
    resultado = []
    def conectado(c, usuario, flags, reason, properties):
        resultado.append(reason)
        pronto.set()
    client.on_connect = conectado
    try:
        client.connect(host, 8883, keepalive=10)
        client.loop_start()
        if not pronto.wait(5):
            raise Orientacao(f"A conta {conta} não recebeu resposta do broker em 5 s.")
        if resultado[0].is_failure:
            raise Orientacao(f"A conta {conta} foi recusada. Confira se foi revogada ou rotacionada; "
                             "consulte marco-3/broker/README.md.")
    finally:
        client.disconnect()
        client.loop_stop()


def diagnostico(host):
    """Só consulta o ambiente: não baixa dependências, recria contas ou inicia serviços."""
    problemas = []
    def checar(nome, acao):
        try:
            detalhe = acao()
            mostrar(f"[OK] {nome}" + (f": {detalhe}" if detalhe else ""))
            return True
        except (Orientacao, OSError, ImportError, ValueError, importlib.metadata.PackageNotFoundError) as erro:
            mostrar(f"[PENDENTE] {nome}: {erro}")
            problemas.append(nome)
            return False
    mostrar("Diagnóstico do Marco 3 — nenhuma configuração será alterada.")
    checar("Python 3.11+", lambda: sys.version.split()[0])
    pacotes = checar("Dependências Python", lambda: ", ".join(
        f"{nome} {importlib.metadata.version(nome)}" for nome in VERSOES))
    docker = checar("Docker e Compose (Linux)", verificar_docker)
    arquivos = checar("Certificados e perfis locais", verificar_arquivos)
    if docker and arquivos and checar("Canal TLS", lambda: verificar_tls(host)) and pacotes:
        for conta in ("consumidor-sala-204", "painel-sala-204", "mic-injetor"):
            checar(f"Login de {conta}", lambda c=conta: verificar_conta(c, host))
    if problemas:
        mostrar("\nPróximo passo: abra o Docker Desktop e execute 'python marco-3/executar.py preparar'.")
        return 1
    mostrar("\nAmbiente pronto. Execute 'python marco-3/executar.py demo' para ver o fluxo.")
    return 0


def encerrar(processo):
    """Interrompe somente um filho desta execução e permite seu MQTT DISCONNECT."""
    if processo.poll() is None:
        try:
            processo.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            processo.wait(timeout=7)
        except (OSError, subprocess.TimeoutExpired):
            if processo.poll() is None:
                processo.terminate()
                processo.wait(timeout=5)


def executar_fluxo(host, com_esp32=False):
    contas = ["consumidor-sala-204", "painel-sala-204"]
    if not com_esp32:
        contas.append("mic-injetor")
    for conta in contas:
        verificar_conta(conta, host)
    processos, leitores = [], []
    resumo_final = threading.Event()

    def iniciar(nome, script, conta, extras=(), aguardar=True):
        comando = [str(PYTHON), "-u", str(MARCO / script), "--config", str(LOCAL / f"{conta}.env"),
                   "--broker", host, *extras]
        opcoes = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        p = subprocess.Popen(comando, cwd=RAIZ, env=ambiente(), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             text=True, encoding="utf-8", errors="replace", **opcoes)
        processos.append(p)
        pronto = threading.Event()
        def ler():
            for linha in p.stdout:
                mostrar(f"[{nome}] {linha.rstrip()}")
                if "PRONTO" in linha:
                    pronto.set()
                if nome == "PAINEL" and "Resumo final" in linha:
                    resumo_final.set()
        leitor = threading.Thread(target=ler, daemon=True)
        leitor.start()
        leitores.append(leitor)
        if aguardar:
            limite = time.monotonic() + 15
            while not pronto.wait(0.1):
                if p.poll() is not None or time.monotonic() >= limite:
                    raise Orientacao(f"{nome} não ficou pronto. Veja a mensagem acima ou execute o diagnóstico.")
        return p

    mostrar("\nTudo será exibido neste terminal. Use Ctrl+C para encerrar os clientes.")
    mostrar("[CONSUMIDOR] decide a ata e confirma eventos; [PAINEL] apresenta o estado da sala.")
    try:
        extras = [] if com_esp32 else ["--dispositivo", "mic-injetor"]
        consumidor = iniciar("CONSUMIDOR", "consumidor/consumidor.py", "consumidor-sala-204", extras)
        painel = iniciar("PAINEL", "painel/painel.py", "painel-sala-204")
        if com_esp32:
            mostrar("\nAguardando o ESP32 publicar. Ligue a placa ou inicie o Wokwi com gateway privado.")
            mostrar("Passo a passo do simulador: marco-3/WOKWI.md.")
            mostrar("Para usar o relógio deste PC no Wokwi: Ctrl+C e execute 'python marco-3/executar.py esp32 --wokwi'.")
            while consumidor.poll() is None and painel.poll() is None:
                time.sleep(0.5)
            raise Orientacao("Um dos clientes encerrou inesperadamente. Veja as mensagens acima.")
        mostrar("\nDemonstração automática (~30 s): silêncio, fala, repetição, falha do sensor e encerramento.")
        sensor = iniciar("SENSOR", "ferramentas/injetar.py", "mic-injetor", ["demonstracao"], aguardar=False)
        while sensor.poll() is None:
            if consumidor.poll() is not None or painel.poll() is not None:
                raise Orientacao("Um dos clientes encerrou durante a demonstração.")
            time.sleep(0.2)
        if sensor.returncode:
            raise Orientacao("O sensor de demonstração encontrou um problema. Veja a saída [SENSOR].")
        # A conclusão vem do painel real, não de um texto que simula sucesso.
        if not resumo_final.wait(5):
            raise Orientacao("O painel não recebeu o resumo final. Execute o diagnóstico e confira as mensagens acima.")
        mostrar("\nDemonstração concluída. Confira 'Resumo final' na saída [PAINEL].")
    except KeyboardInterrupt:
        mostrar("\nEncerrando os clientes desta execução...")
    finally:
        for p in reversed(processos):
            encerrar(p)
        for leitor in leitores:
            leitor.join(timeout=1)
        for p in processos:
            p.stdout.close()
        mostrar("Broker local mantido ativo. Para pará-lo: python marco-3/executar.py parar")


def menu():
    opcoes = {"1": "preparar", "2": "demo", "3": "diagnostico", "4": "esp32", "5": "parar"}
    while True:
        mostrar("\n=== TowerAI — MARCO 3 ===\n"
                "1. Preparar / conferir a instalação local\n"
                "2. Ver a demonstração completa sem ESP32 (recomendado para começar)\n"
                "3. Diagnosticar o ambiente\n"
                "4. Acompanhar ESP32 / Wokwi\n"
                "5. Parar o broker local\n"
                "0. Sair do menu")
        try:
            escolha = input("Escolha uma opção: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if escolha == "0":
            return 0
        if escolha not in opcoes:
            mostrar("Digite um número entre 0 e 5.")
            continue
        processo = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), opcoes[escolha]],
                                    cwd=RAIZ, env=ambiente())
        try:
            processo.wait()
        except KeyboardInterrupt:
            # O console entrega Ctrl+C também à execução escolhida. Aguardar
            # seu finally permite voltar ao menu depois de fechar os clientes.
            mostrar("\nAguardando o encerramento da execução para voltar ao menu...")
            try:
                processo.wait(timeout=25)
            except subprocess.TimeoutExpired:
                encerrar(processo)


def main():
    preparar_interrupcao()
    ap = argparse.ArgumentParser(description="Executar o Marco 3: broker local + consumidor + painel")
    sub = ap.add_subparsers(dest="acao")
    for acao, descricao in (("preparar", "preparar ambiente e iniciar o broker, sem trocar segredos existentes"),
                            ("demo", "mostrar o fluxo inteiro em um terminal, sem ESP32"),
                            ("esp32", "iniciar consumidor e painel para a placa/Wokwi"),
                            ("diagnostico", "verificar o ambiente sem alterá-lo")):
        parser = sub.add_parser(acao, help=descricao)
        parser.add_argument("--broker", default="localhost", help="endereço local do broker; padrão localhost")
        if acao == "preparar":
            parser.add_argument("--ip", action="append", default=[], help="SAN LAN no primeiro provisionamento")
        if acao == "esp32":
            parser.add_argument("--wokwi", action="store_true", help="inclui relógio NTP local para o simulador")
    sub.add_parser("parar", help="parar o broker preservando certificados e dados")
    sub.add_parser("logs", help="acompanhar o log do broker (Ctrl+C volta ao terminal)")
    args = ap.parse_args()
    if sys.version_info < (3, 11):
        raise Orientacao("Instale Python 3.11 ou superior para executar o Marco 3.")
    if not args.acao:
        return menu()
    if args.acao in ("preparar", "demo", "esp32"):
        verificar_docker()
        preparar_python()
    # Reexecuta no ambiente correto sem exigir Activate.ps1 ou política de
    # execução do PowerShell. Diagnóstico não instala o que estiver faltando.
    if args.acao in ("preparar", "demo", "esp32", "diagnostico") and PYTHON.exists():
        # Em Linux, bin/python costuma ser symlink do Python global. O prefixo
        # identifica o ambiente corretamente mesmo quando os executáveis resolvem iguais.
        if Path(sys.prefix).resolve() != (RAIZ / ".venv").resolve():
            return subprocess.run([str(PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
                                  cwd=RAIZ, env=ambiente()).returncode
    if args.acao == "diagnostico":
        return diagnostico(args.broker)
    if args.acao in ("preparar", "demo", "esp32"):
        iniciar_broker(args.broker, getattr(args, "ip", ()))
        if args.acao == "preparar":
            mostrar("\nPreparação concluída. Agora execute: python marco-3/executar.py demo")
        else:
            with ExitStack() as recursos:
                if getattr(args, "wokwi", False):
                    if not (MARCO / "produtor/build-wokwi/sketch.ino.merged.bin").is_file():
                        raise Orientacao("Compile primeiro o firmware da simulação: "
                                         "python marco-3/ferramentas/compilar_produtor.py --wokwi. "
                                         "Passo a passo em marco-3/WOKWI.md.")
                    from ferramentas.ntp_local import RelogioLocal
                    try:
                        recursos.enter_context(RelogioLocal())
                    except OSError as erro:
                        raise Orientacao("Não foi possível abrir NTP em 127.0.0.1:4123/UDP. "
                                         "A porta pode estar ocupada ou sem permissão. "
                                         "Consulte marco-3/WOKWI.md; não desligue a validação TLS. "
                                         f"Detalhe: {erro}") from None
                    mostrar("[OK] NTP local ativo em 127.0.0.1:4123/UDP; usa a hora deste PC.")
                    mostrar("No VS Code: selecione marco-3/produtor/wokwi.toml e inicie o simulador.")
                executar_fluxo(args.broker, args.acao == "esp32")
    elif args.acao == "parar":
        verificar_docker()
        rodar(COMPOSE + ["stop"])
        mostrar("Broker parado. Certificados, contas e dados preservados.")
    elif args.acao == "logs":
        verificar_docker()
        rodar(COMPOSE + ["logs", "--tail", "60", "--follow"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Orientacao as erro:
        mostrar(f"\n[COMO RESOLVER] {erro}")
        sys.exit(1)
    except (subprocess.CalledProcessError, OSError) as erro:
        mostrar(f"\n[COMO RESOLVER] Não foi possível concluir a operação: {erro}")
        mostrar("Execute o diagnóstico ou consulte marco-3/README.md.")
        sys.exit(1)
    except KeyboardInterrupt:
        mostrar("\nOperação interrompida. O broker pode continuar ativo; use a opção 'parar' se necessário.")
        sys.exit(130)

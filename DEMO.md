# TowerAI — Guia de demonstração

Aplicativo web **offline** que cria seu próprio serviço numa rede local: salas de
chat compartilhadas com um assistente de IA local, transcrição ao vivo
(acessibilidade), microserviço de transcrição para dispositivos IoT, atas de
reunião pesquisáveis e um mapa de rede com análise por IA. Tudo roda sem internet
(exceto a instalação inicial das dependências).

## 1. Executar (caminho rápido)

**Windows (PowerShell):**
```powershell
.\run.ps1
```
**Linux/macOS:**
```bash
bash run.sh
```

O script cria o ambiente virtual, instala as dependências, semeia dados de
demonstração e inicia o servidor. Depois acesse **http://localhost:5000**.

> Login de demonstração: **demo / demo1234**

Se o pacote não incluir o modelo de voz, baixe-o uma vez:
```bash
python demo_setup.py --download-model
```

## 2. Fornecer a rede local (custo zero)

O app **não cria** o Wi-Fi — ele roda sobre uma rede existente. Use o mais fácil:
- **Reaproveitar um roteador Wi-Fi antigo** como ponto de acesso (sem internet).
- **Hotspot Móvel do Windows** (substituto moderno do `netsh hostednetwork`).

Rode o TowerAI numa máquina conectada a essa rede; os dispositivos acessam pelo IP
ou por `towerai.local`.

## 3. O que demonstrar

### 💬 Chat colaborativo + IA
Abra `http://localhost:5000`, entre com **demo/demo1234**. Digite mensagens na sala
`#general`. Para chamar a IA, comece a mensagem com **@tower** ou marque a caixa
**AI**. Abra em dois navegadores para ver mensagens em tempo real (WebSocket).
> A IA precisa de um modelo local (LM Studio ou Ollama na porta 1234). Sem ele, o
> chat funciona e a IA responde "indisponível" — nada quebra.

### 🟢 Legendas ao vivo (acessibilidade)
Na barra lateral, **Live Captions**. Clique **Start captioning** e fale (pt-BR) —
a transcrição aparece em tempo real e é vista por todos na sala. Ideal para
participantes surdos/ensurdecidos.
> O microfone do navegador exige HTTPS fora de `localhost`. Gere um certificado:
> `python serve.py --gen-cert` e rode com ele (veja README).

### 📟 Dispositivos IoT (microserviço de transcrição)
Barra lateral → **IoT Devices**. Já existe o dispositivo `demo-esp32`. Crie outro
para ver a chave de API. Simule um dispositivo enviando áudio:
```bash
# Lote (envia um WAV e recebe o texto):
python examples/iot_client.py batch --key SUA_CHAVE --wav audio.wav --room general

# Streaming (frames PCM16 16kHz em tempo real):
python examples/iot_client.py stream --key SUA_CHAVE --wav audio16k.wav --room general
```
As legendas do dispositivo aparecem na página **Live Captions** da sala.

### 📝 Atas de reunião (pesquisáveis)
Barra lateral → **Meetings**. Há uma "Reunião de demonstração" de exemplo — abra e
pesquise por palavras. Na página de legendas, o botão **● Record** grava tudo que
for transcrito na sala; depois **exporte em .txt**.

### 🌐 Mapa de rede (Fase 3)
Barra lateral → **Network Map** → **Scan now**. Ele descobre os dispositivos da sua
rede (IP, fabricante pelo MAC, latência, portas, nomes mDNS) e desenha o mapa.
Adicione um aparelho à rede e clique **Scan** de novo: ele aparece em **"+ new"**
(histórico e comparação entre scans). **Analyze with AI** pede recomendações ao
modelo local (apenas sugestões, nunca executa nada).

## 4. Contas e credenciais

- Usuário de demonstração: **demo / demo1234** (crie outros em `/register`).
- As chaves de API dos dispositivos são mostradas **uma única vez** ao criar.
- `SECRET_KEY` e o banco `towerai.db` são gerados na primeira execução.

## 5. Configuração (opcional, via .env)

Copie `.env.example` para `.env`. Destaques:
- `LLM_BASE_URL` — endpoint do modelo local (padrão `http://127.0.0.1:1234`).
- `STT_ENGINE` — `vosk` (padrão, CPU, streaming) ou `whisper` (GPU, mais preciso).
- `VOSK_MODEL_PATH` — ou apenas coloque uma pasta `vosk-model-*` em `models/`.
- `USE_TLS=1` — HTTPS (necessário para microfone de dispositivos remotos).
- `REDIS_URL` — para escalar entre múltiplos processos (gunicorn) no Linux.

## 6. Estrutura

Veja `README.md` para a tabela de arquivos e detalhes de arquitetura. Recursos
opcionais vivem em `features/` e carregam sozinhos quando as dependências existem.

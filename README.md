# 🤖 Bauer Agent

Runtime adaptativo para LLMs locais e cloud.

> Hermes é rígido. Bauer é adaptativo.
> Roda com o que tem, ajusta o que precisar, avisa claramente.

---

## 📋 Índice

- [⚡ Instalação](#instalação)
- [⚙️ Configuração](#configuração)
- [🧠 bauer agent](#bauer-agent)
- [🌐 bauer serve](#bauer-serve)
- [💬 bauer gateway — canais de chat](#bauer-gateway--canais-de-chat-telegram-discord)
- [🔌 bauer gateway-ws (Claw3D)](#bauer-gateway-ws-claw3d)
- [🔗 Providers suportados](#providers-suportados)
- [🛠️ Tools disponíveis](#tools-disponíveis)
- [🐳 Docker](#docker)
- [🧪 Desenvolvimento](#desenvolvimento)

---

## ⚡ Instalação

### 🐧 Linux / macOS — instalação automática

```bash
curl -fsSL https://raw.githubusercontent.com/henriqueferraz86/bauer-agent/master/install.sh | bash
```

Instala em `~/.local/share/bauer-agent`, cria o comando `bauer` em `~/.local/bin` e adiciona ao PATH automaticamente.

```bash
# Atualizar instalação existente
curl -fsSL .../install.sh | bash -s -- --update

# Remover completamente
curl -fsSL .../install.sh | bash -s -- --uninstall
```

### 🪟 Windows — instalação automática

```powershell
irm https://raw.githubusercontent.com/henriqueferraz86/bauer-agent/master/install.ps1 | iex
```

Instala em `%LOCALAPPDATA%\BauerAgent`, cria `bauer.cmd` e adiciona ao PATH do usuário.

```powershell
# Atualizar
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/henriqueferraz86/bauer-agent/master/install.ps1))) -Update

# Remover
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/henriqueferraz86/bauer-agent/master/install.ps1))) -Uninstall
```

> **Dica**: Se já tiver o arquivo `install.ps1` localmente, use `.\install.ps1 -Update` ou `.\install.ps1 -Uninstall` diretamente.

> **🔒 Nota Windows**: ao digitar API keys no seletor de modelos, o campo está mascarado — o texto não aparece enquanto você digita (comportamento normal do `getpass`).

### 🔧 Instalação manual (dev / contribuição)

```bash
git clone https://github.com/henriqueferraz86/bauer-agent.git
cd bauer-agent
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[gateway]"
bauer doctor
```

---

## ⚙️ Configuração

### 1. Copie o `.env.example`

```bash
cp .env.example .env
```

Preencha as API keys dos providers que vai usar. Exemplo:

```env
GROQ_API_KEY=gsk_...
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
DEEPSEEK_API_KEY=sk-...
```

### 2. Escolha o provider/modelo

```bash
bauer model
```

O seletor interativo lista todos os providers disponíveis, solicita a API key (se necessário) e salva a configuração em `config.yaml`.

### 3. Diagnóstico

```bash
bauer doctor
```

Verifica: provider ativo, modelo disponível, RAM, contexto aplicado, tool mode.

---

## 🧠 bauer agent

O **bauer agent** é o modo interativo principal — um assistente com memória, tools e suporte a agents especializados.

### 💬 Chat básico

```bash
bauer chat
```

Inicia sessão interativa com o modelo configurado. A sessão é salva automaticamente e retomada na próxima execução.

**Opções:**

```bash
bauer chat --model qwen2.5-coder:7b   # força modelo específico
bauer chat --resume                    # retoma última sessão explicitamente
bauer chat --no-intro                  # pula a tela de introdução
```

### 🖥️ Terminal UI (TUI)

Interface gráfica dentro do terminal, com histórico navegável, temas e suporte a streaming:

```bash
bauer tui                        # tema padrão
bauer tui --theme dark           # tema escuro
bauer tui --theme mono           # tema monocromático
bauer tui --workspace ./meu-dir  # workspace personalizado
```

Requer: `pip install prompt-toolkit` (incluído nos extras `gateway` e `all`).

### 🤖 Agents especializados

Agents são perfis com system prompt, ferramentas e modelo próprios, definidos em `agents.yaml`.

```bash
# Listar agents disponíveis
bauer agent list

# Criar novo agent (wizard interativo)
bauer agent create

# Iniciar agent
bauer agent run <nome>

# Exemplos:
bauer agent run python
bauer agent run data-analyst
bauer agent run henrique-ferraz
```

Cada agent tem seu próprio histórico de sessão (`agent-<nome>.jsonl`) — retoma automaticamente de onde parou. 🔄

**Estrutura de um agent (`agents.yaml`):**

```yaml
- name: python
  description: Especialista Python senior
  model: qwen2.5-coder:7b        # opcional — sobrescreve config.yaml
  provider: ollama                # opcional
  tools:
    - read_file
    - write_file
    - run_command
    - glob_files
  system: |
    Você é um engenheiro Python sênior...
```

### 🏢 Empresas (multi-tenant local)

Cada empresa tem workspace, memória e sessions isoladas:

```bash
bauer company create      # wizard de criação
bauer company list        # lista empresas
bauer company use <slug>  # ativa empresa
bauer company info <slug> # detalhes
```

Com empresa ativa, `bauer chat` e `bauer agent run` usam automaticamente o workspace isolado dela.

### 🔀 Orquestrador multi-passo

```bash
bauer orchestrate run "pesquise sobre Python 3.13 e crie um resumo"
bauer orchestrate run "analise os arquivos do projeto e gere relatório" --interactive
```

O orquestrador planeja a tarefa em passos com DAG de dependências, executa passos independentes em paralelo ⚡ e salva progresso em disco.

### ⌨️ Comandos dentro da sessão

| Comando | Descrição |
|---|---|
| `/model` | 🔄 Troca provider/modelo ao vivo (sem reiniciar) |
| `/status` | 📊 Tokens usados, budget e modelo atual |
| `/clear` | 🗑️ Limpa histórico da sessão |
| `/sessions` | 📁 Lista sessões salvas |
| `/memory` | 🧠 Lista arquivos de memória do agent |
| `/memory search <query>` | 🔍 Busca semântica na memória |
| `/memory note <texto>` | 📝 Adiciona nota à memória |
| `/project` | 📂 Exibe PROJECT.md e resumo de tarefas |
| `/kanban` | 📋 Exibe board de tarefas (TASKS.md) |
| `/task add <título>` | ➕ Adiciona tarefa ao Kanban |
| `/task start <id>` | ▶️ Marca tarefa como em andamento |
| `/task done <id>` | ✅ Conclui tarefa |
| `/spec list` | 📄 Lista specs do projeto |
| `/spec new` | ✨ Cria novo spec (wizard) |
| `/agents` | 🤖 Lista agents disponíveis |
| `/exit` | 👋 Encerra a sessão |

---

## 🌐 bauer serve

O **bauer serve** expõe o Bauer como uma API HTTP REST + Web UI, permitindo integração com outras aplicações, automações e uso remoto.

### 🚀 Iniciar o servidor

```bash
bauer serve
# Padrão: http://localhost:7770

bauer serve --port 8080
bauer serve --host 0.0.0.0 --port 7770   # aceita conexões externas
```

A Web UI fica disponível em `http://localhost:7770` (interface de chat no browser). 🖥️

### 🔑 Autenticação

Configure a API key no `config.yaml`:

```yaml
serve:
  api_key: "sua-chave-secreta"
```

Ou defina na variável de ambiente `BAUER_API_KEY`. Se vazio, auth é desabilitada.

Envie em toda requisição autenticada:

```bash
# Via header
curl -H "X-API-Key: sua-chave-secreta" http://localhost:7770/chat ...

# Via Authorization Bearer
curl -H "Authorization: Bearer sua-chave-secreta" http://localhost:7770/chat ...
```

### 🚦 Rate limiting

```yaml
serve:
  rate_limit:
    requests: 60    # requisições por janela
    window_s: 60    # janela em segundos
```

Retorna `429 Too Many Requests` com header `Retry-After` quando excedido. Desative com `requests: 0`.

### 📡 Endpoints

#### 🔓 Públicos (sem auth)

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/health` | ❤️ Liveness check — `{"status": "ok", "model": "..."}` |
| `GET` | `/status` | 📊 Modelo, contexto, tools disponíveis |
| `GET` | `/tools` | 🛠️ Lista tools com schema |
| `GET` | `/v1/models` | 📋 Lista modelos (OpenAI-compat) |
| `GET` | `/metrics` | 📈 Métricas Prometheus (text/plain) |

#### 🔒 Autenticados

| Método | Endpoint | Descrição |
|---|---|---|
| `POST` | `/chat` | 💬 Envia mensagem, recebe resposta completa |
| `GET` | `/stream` | ⚡ Resposta em tempo real via SSE |
| `GET` | `/sessions` | 📁 Lista sessões ativas |
| `DELETE` | `/sessions/{id}` | 🗑️ Remove sessão |
| `POST` | `/v1/chat/completions` | 🔗 OpenAI-compatible (batch ou stream) |
| `POST` | `/models/switch` | 🔄 Troca modelo ao vivo |

#### 🧪 Exemplos de uso

```bash
# 💬 Chat simples
curl -X POST http://localhost:7770/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sua-chave" \
  -d '{"message": "Olá!", "session_id": "minha-sessao"}'

# ⚡ Streaming (SSE)
curl "http://localhost:7770/stream?message=Olá&session_id=s1" \
  -H "X-API-Key: sua-chave"

# 🔗 OpenAI-compatible (compatível com qualquer cliente OpenAI)
curl -X POST http://localhost:7770/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sua-chave" \
  -d '{
    "model": "bauer",
    "messages": [{"role": "user", "content": "Olá!"}],
    "stream": true
  }'

# 📈 Métricas Prometheus
curl http://localhost:7770/metrics
```

#### 📈 Métricas Prometheus disponíveis

```
bauer_uptime_seconds          — ⏱️ tempo online
bauer_requests_total          — 📊 total de requisições HTTP
bauer_requests_errors_total   — ❌ erros 5xx
bauer_chat_requests_total     — 💬 chamadas ao /chat
bauer_stream_requests_total   — ⚡ chamadas ao /stream
bauer_tool_calls_total        — 🛠️ tool calls executadas
bauer_rate_limited_total      — 🚦 requisições bloqueadas por rate limit
```

### 🔗 Integração com clientes OpenAI-compatible

O `bauer serve` expõe `/v1/chat/completions` no formato OpenAI SSE — funciona com qualquer cliente que suporte a API OpenAI (LangChain, LlamaIndex, Open WebUI, etc.).

---

## 💬 bauer gateway — canais de chat (Telegram, Discord…)

O **Bauer Gateway** conecta o agent a canais de chat: você conversa com o Bauer
pelo Telegram ou Discord, com sessão persistente por chat, e o agent pode
enviar notificações a canais via tool `channel_send`.

### 🚀 Setup em 3 passos

```bash
bauer gateway init     # wizard: token, validação live, allowlist, .env
bauer gateway start    # sobe todos os canais habilitados + entrega do outbox
bauer gateway status   # canais, tokens, allowlists, outbox
bauer gateway stop     # encerra o gateway (e bridges antigos órfãos)
```

> Se o bot responder com um menu antigo ou der erro 409, há um bridge órfão
> de versão anterior rodando — `bauer telegram stop` resolve.

### 📱 Telegram

1. Crie um bot com o [@BotFather](https://t.me/BotFather) e copie o token.
2. `bauer gateway init` → cole o token → envie `/start` ao bot para o wizard
   descobrir seu user id (allowlist automática).
3. `bauer gateway start` (ou `bauer telegram start` para só este canal).

### 🎮 Discord

1. [Developer Portal](https://discord.com/developers/applications) → New
   Application → Bot → copie o token.
2. Aba **Bot** → habilite **MESSAGE CONTENT INTENT**.
3. Convide o bot (OAuth2 → URL Generator → scope `bot` → Send Messages).
4. `bauer gateway init` → cole o token e seu user id.
5. Requer extra: `pip install 'bauer-agent[gateway]'` (websockets).

Em servidores o bot responde só quando **mencionado** (`mention_only: true`);
DMs respondem sempre. Allowlists de usuário/guild/canal no `config.yaml`.

### ⚙️ Config (config.yaml)

```yaml
telegram:
  enabled: true
  allowed_users: [123456789]    # vazio = NEGA todo mundo (seguro por default)
discord:
  enabled: true
  allowed_users: ["111222333444555666"]
  mention_only: true
gateway:
  outbox_drain_interval_s: 15   # frequência de entrega do outbox
```

Tokens ficam no `.env` (`TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`) — nunca no
config.yaml em produção.

### 📤 Notificações do agent (tool channel_send)

```bash
# registra um canal de notificação (telegram/discord/slack/webhook/file)
bauer gateway-channel-add alerts telegram 123456789
```

No chat, o agent pode usar `channel_send` — a mensagem entra no **outbox
durável** (SQLite, retry automático) e é entregue pelo `bauer gateway start`.

Comandos dentro do chat: `/status`, `/clear`, `/help`.

---

## 🔌 bauer gateway-ws (Claw3D)

O **bauer gateway-ws** é uma camada WebSocket que faz bridge entre clientes WebSocket e o `bauer serve` (HTTP).

### 🏗️ Arquitetura

```
🖥️  Cliente WebSocket
        ↕  ws://localhost:18789
🔌  bauer gateway-ws
        ↕  http://localhost:7770
🌐  bauer serve
        ↕
🤖  LLM (Ollama / Groq / OpenAI / etc.)
```

### 🚀 Iniciar

```bash
# bauer serve precisa estar rodando primeiro
bauer serve &

# Depois inicia o gateway
bauer gateway-ws
# Padrão: ws://localhost:18789 → http://localhost:7770

bauer gateway-ws --port 18789 --bauer-url http://localhost:7770
```

### 📡 Eventos WebSocket suportados

| Evento | Direção | Descrição |
|---|---|---|
| `chat.send` | ← cliente | 💬 Envia mensagem; inicia resposta em streaming |
| `chat.abort` | ← cliente | ⛔ Cancela resposta em andamento |
| `chat.history` | ← cliente | 📜 Solicita histórico da sessão |
| `agents.list` | ← cliente | 🤖 Lista agents disponíveis |
| `sessions.list` | ← cliente | 📁 Lista sessões |
| `sessions.reset` | ← cliente | 🗑️ Limpa histórico de sessão |
| `models.list` | ← cliente | 📋 Lista modelos disponíveis |
| `status` | ← cliente | 📊 Status do servidor |
| `heartbeat` | → cliente | 💓 Keepalive a cada 25s |

### ⚡ Streaming de chat

Cada chunk de texto do LLM é emitido como evento WebSocket em tempo real:

```
cliente → chat.send {message: "Olá"}
gateway → res ok    {status: "started", runId: "abc123"}
gateway → event     {type: "delta", content: "Ol"}
gateway → event     {type: "delta", content: "á!"}
gateway → event     {type: "final", content: "Olá! Como posso ajudar?"}
```

### 🔑 Configuração de API key

```bash
bauer gateway-ws --api-key sua-chave-secreta
```

O gateway repassa a key automaticamente para o `bauer serve` em todas as requisições.

---

## 🔗 Providers suportados

### ✅ Gratuitos (sem billing)

| Provider | Variável de ambiente | Notas |
|---|---|---|
| 🖥️ **Ollama** (local) | — | Modelos locais; sem custo; requer Ollama rodando |
| ☁️ **OpenCode Zen** | — | Modelos gratuitos via opencode.ai; sem API key |
| ⚡ **Groq** | `GROQ_API_KEY` | Llama 3.3 70B ultra-rápido; tier gratuito generoso (`console.groq.com`) |
| 🐙 **GitHub Models** | `GITHUB_TOKEN` | GPT-4o, Llama via GitHub Marketplace |

### 💳 Pagos (requerem billing / API key)

| Provider | Variável de ambiente | Notas |
|---|---|---|
| 🟢 **OpenAI** | `OPENAI_API_KEY` | GPT-4o, o1, etc. (`platform.openai.com`) |
| 🟣 **Anthropic** | `ANTHROPIC_API_KEY` | Claude Haiku, Sonnet, Opus |
| 🔵 **Google Gemini** | `GEMINI_API_KEY` | Gemini 1.5 Pro/Flash |
| 🔀 **OpenRouter** | `OPENROUTER_API_KEY` | Agregador — acesso a +200 modelos |
| 🟠 **Mistral** | `MISTRAL_API_KEY` | Mistral Large, Codestral |
| ✖️ **xAI** | `XAI_API_KEY` | Grok 3 |
| 🤝 **Together AI** | `TOGETHER_API_KEY` | Llama, Qwen e outros open-source |
| 🐋 **DeepSeek** | `DEEPSEEK_API_KEY` | DeepSeek-V3, R1 |
| ☁️ **Azure OpenAI** | `AZURE_OPENAI_API_KEY` | GPT via Azure |
| 🐙 **GitHub Copilot** | — | Auth via Device Flow do GitHub |
| 🔧 **LM Studio / vLLM** | — | Qualquer endpoint OpenAI-compatible |

> Use `bauer model` para selecionar provider e modelo interativamente. O menu exibe claramente quais são GRÁTIS e quais são PAGOS.

---

## 🛠️ Tools disponíveis

### 📁 Arquivo
| Tool | Descrição |
|---|---|
| `list_dir` | 📂 Lista arquivos e diretórios |
| `read_file` | 📖 Lê conteúdo de arquivo |
| `write_file` | ✏️ Escreve/sobrescreve arquivo |
| `append_file` | ➕ Adiciona conteúdo ao final |
| `create_dir` | 📁 Cria diretório |
| `delete_file` | 🗑️ Remove arquivo |
| `move_file` | 📦 Move ou renomeia arquivo |
| `diff_files` | 🔍 Compara dois arquivos |
| `search_text` | 🔎 Busca texto em arquivo |

### 🔍 Busca
| Tool | Descrição |
|---|---|
| `glob_files` | 🌐 Encontra arquivos por padrão glob |
| `regex_search` | 🔬 Busca com regex em arquivos |

### ⚙️ Utilidade
| Tool | Descrição |
|---|---|
| `calculate` | 🧮 Avalia expressão matemática |
| `datetime_now` | 🕐 Data e hora atual |
| `json_query` | 📊 Consulta JSON com path |
| `encode_decode` | 🔐 Base64, URL encoding, hash |

### 🔓 Opcionais
| Tool | Descrição | Requer |
|---|---|---|
| `run_command` | 💻 Executa comando shell | config `allow_shell: true` |
| `web_search` | 🌐 Busca na web | `SERPAPI_KEY` ou similar |
| `web_fetch` | 📥 Faz GET em URL | — |
| `http_request` | 🌍 HTTP GET/POST genérico | — |

---

## 🐳 Docker

```bash
# Sobe Bauer + Ollama no mesmo container
docker compose up -d

# Logs
docker compose logs -f

# API disponível em http://localhost:8000
# O modelo padrão (qwen2.5-coder:3b) é baixado automaticamente no primeiro boot 🚀
```

Para mudar o modelo padrão:

```yaml
# docker-compose.yml
environment:
  - BAUER_MODEL=llama3.2:3b
```

---

## 🧪 Desenvolvimento

```bash
# Instalar com dependências de dev
pip install -e ".[server]"
pip install pytest pytest-cov

# Rodar todos os testes
pytest

# Cobertura
pytest --cov=bauer --cov-report=term-missing

# Diagnóstico completo
bauer doctor
bauer doctor --providers   # testa conectividade de todos os providers
```

---

## 💡 Princípio do projeto

> Subir sem dor é mais importante que ter muitas features.

Ordem: confiável → adaptativo → aprendiz → especializado. 🚀

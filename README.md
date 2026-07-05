# 🤖 Bauer Agent

Runtime adaptativo para LLMs locais e cloud.

> Hermes é rígido. Bauer é adaptativo.
> Roda com o que tem, ajusta o que precisar, avisa claramente.

---

## 📋 Índice

- [⚡ Instalação](#instalação)
- [⚙️ Configuração](#configuração)
- [🧠 Modos de uso](#modos-de-uso) — chat · agent · App Factory · /loop · especialistas · skills
- [🌐 bauer serve](#bauer-serve)
- [💬 bauer gateway — canais de chat](#bauer-gateway--canais-de-chat-telegram-discord-slack)
- [🔌 bauer gateway-ws (Claw3D)](#bauer-gateway-ws-claw3d)
- [🔗 Providers suportados](#providers-suportados)
- [🛠️ Tools disponíveis](#tools-disponíveis)
- [🎛️ Toggles de comportamento](#toggles-de-comportamento-configyaml)
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
curl -fsSL https://raw.githubusercontent.com/henriqueferraz86/bauer-agent/master/install.sh | bash -s -- --update

# Remover completamente
curl -fsSL https://raw.githubusercontent.com/henriqueferraz86/bauer-agent/master/install.sh | bash -s -- --uninstall
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

### 🚀 Primeiros passos após instalar

```powershell
# 1. Configurar provider e modelo (wizard interativo)
bauer init

# 2. Verificar saúde do ambiente
bauer doctor

# 3. Iniciar — escolha o modo:
bauer chat              # chat direto com o modelo
bauer agent run <nome>  # agent especializado (com tools, system prompt próprio)
bauer agent list        # ver agents disponíveis
```

> **Dica**: use `bauer model` a qualquer momento para trocar de provider/modelo. O menu exibe claramente quais são **GRÁTIS** e quais são **PAGOS**.

### 🧭 Perdido? Comece por aqui

Digite só **`bauer`** (sem nada): a tela de boas-vindas detecta seu estado e mostra o próximo passo certo — *sem config* → `bauer init`; *falta chave* → `bauer model`; *pronto* → `bauer agent`. E o próprio `bauer init` se oferece para abrir o agente na hora.

```bash
bauer          # tela de boas-vindas inteligente (por onde começar)
bauer start    # mesma tela, a qualquer momento
bauer guide    # tour rápido pelos modos (chat / agent / model / gateway)
```

### 🔧 Instalação manual (dev / contribuição)

```bash
git clone https://github.com/henriqueferraz86/bauer-agent.git
cd bauer-agent
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[gateway]"
bauer doctor
```

**Extras opcionais:**

| Extra | Instala | Para quê |
|-------|---------|----------|
| `[web]` | `ddgs`, `beautifulsoup4` | busca web geral (DuckDuckGo) + extração de conteúdo |
| `[server]` | `fastapi`, `uvicorn` | `bauer serve` (API HTTP) |
| `[gateway]` | + `websockets` | canais Telegram/Discord + `bauer shell` |
| `[keychain]` | `keyring` | guardar credenciais no keychain do SO |
| `[all]` | tudo acima | — |

> Busca web **sem nenhum extra**: o backend **Wikipedia** (open-source, sem chave)
> funciona só com as dependências core e é o fallback automático do `web_search`.
> Para busca geral, `pip install -e ".[web]"`.

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

## 🧠 Modos de uso

O Bauer tem três modos de interação. Escolha o certo para cada situação:

| Comando | Tools | Memória | Agents | Quando usar |
|---|---|---|---|---|
| `bauer chat` | ❌ | ❌ | ❌ | Testar o modelo puro, sem nenhuma ferramenta |
| `bauer agent` | ✅ | ✅ | padrão | **Uso diário** — assistente completo |
| `bauer agent run <nome>` | ✅ | ✅ | especializado | Tarefa específica com perfil dedicado |

### 💬 bauer chat — modo mínimo

Chat direto com o modelo, **sem tools, sem workspace, sem memória persistente**. Útil para testar o modelo puro ou quando não precisa de ferramentas.

```bash
bauer chat
bauer chat --model qwen2.5-coder:7b   # força modelo específico
bauer chat --resume                    # retoma última sessão
bauer chat --no-intro                  # pula a tela de introdução
```

### 🤖 bauer agent — uso diário (recomendado)

**Chat completo** com tools, sessão persistente, workspace e slash commands. É o modo principal do Bauer.

```bash
bauer agent                  # inicia com o model do config.yaml
bauer agent --resume         # retoma última sessão
bauer agent --model gpt-4o   # força modelo específico
```

### 🎯 bauer agent run — agent especializado

Agent com **perfil dedicado**: system prompt próprio, tools específicas, modelo próprio e histórico separado. Definidos em `agents.yaml`.

```bash
bauer agent list                  # lista agents disponíveis
bauer agent create                # cria novo agent (wizard)
bauer agent run python            # agent especialista em Python
bauer agent run data-analyst      # agent analista de dados
bauer agent run henrique-ferraz   # agent personalizado
```

Cada agent retoma automaticamente de onde parou (histórico em `agent-<nome>.jsonl`).

> **Resumo prático:**
> - Quer só conversar → `bauer chat`
> - Quer usar tools e memória → `bauer agent` ← **use este no dia a dia**
> - Quer um perfil especializado → `bauer agent run <nome>`

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

### 🏭 App Factory — da ideia à V1 com governança

A **App Factory** transforma uma ideia em uma aplicação V1 funcional com *quality
gates executáveis* — não é só orientação no prompt: enquanto o projeto está sob
governança, a própria ToolRouter **recusa escrever código** antes da
especificação existir.

```bash
bauer factory init "app de recomendações de investimento" --path bauerinvest
bauer factory status    # gate atual, docs pendentes, delivery score
bauer factory gate      # o que falta para liberar código
bauer factory score     # delivery score objetivo (0–10)
```

Ou direto no `bauer agent`: descreva a ideia e o Bauer chama `app_factory_init`
sozinho. O fluxo:

1. **Discovery** — a IA **rascunha** os 7 docs de planejamento (SPEC, ARCHITECTURE,
   BACKLOG, TASKS, DECISIONS, PROJECT_CONTEXT, PROGRESS) a partir da ideia,
   marcando o que assumiu como *"Premissa"*. Só pergunta (`clarify`) se algo
   essencial estiver genuinamente ambíguo — nada de interrogatório.
2. **Gate** — quando os 7 docs estão preenchidos, o gate vira `IMPLEMENTATION` e o
   Bauer oferece um **checkpoint**: `[R]` revisar os docs, `[D]` desenvolver
   (dispara o `/loop` autônomo e pode semear o kanban a partir do BACKLOG),
   `[C]` continuar manual.
3. **Verificação** — `verify_app` builda/roda o app de verdade; o delivery score
   só sobe quando ele passa ("arquivos existem" ≠ "funciona").

Cada ideia vive na **sua pasta** (`--path`), e a escrita fica contida nela —
nada solto na raiz do workspace. Projetos completos nunca são sobrescritos.

### 🔁 /loop — modo autônomo

Dentro do `bauer agent`, o `/loop` roda o agente **sozinho, turno após turno**,
sem confirmação a cada passo — até concluir a tarefa, estourar o orçamento de
segurança, um guardrail mandar parar, ou você apertar Ctrl+C.

```
/loop implemente a V1 seguindo os docs, rode verify_app a cada fatia
      --max-minutes 90 --max-tool-calls 600 --max-cost 0.50
```

Guardrails de segurança embutidos (orçamento de tempo/tool-calls/custo,
detecção de loop, aprovação de comandos perigosos). **loop-skills** (`~/.bauer/
loop_skills/`) permitem auto-disparar um `/loop` quando o input casa um padrão —
liste/rode com `/loop-skill list` e `/loop-skill run <nome>`.

### 🧑‍🔧 Especialistas — delegação automática

O Bauer traz **10 agents especialistas embutidos** (code, devops, security, data,
research, writing, sre, design, finance, productivity). O modelo pode delegar uma
consulta pontual a um deles via a tool `delegate_task` — com `agent_name` explícito
ou deixando o Bauer **auto-selecionar** o melhor por relevância. Veja todos com
`/agents` (builtins + os seus de `~/.bauer/agents.yaml`). Toggle:
`agent.specialist_delegation` no config.

### ✨ Skills — catálogo que dispara sozinho

O Bauer traz um catálogo de **skills** (procedimentos/guias) e as injeta
**automaticamente** no contexto quando sua mensagem casa uma delas com confiança —
sem você precisar invocar nada. Na dúvida, não injeta (falha seguro).

```bash
bauer skills-hub list             # catálogo built-in
bauer skills-hub search <termo>   # busca
bauer skills-hub install <slug>   # instala em ~/.bauer/skills
bauer skills-hub stats            # telemetria de uso (quais disparam, desfecho, 👍/👎)
```

Toggle: `agent.skill_auto_inject` no config. A telemetria é só observação (não
age) — base para refinar skills por uso real.

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
| `/spec list` · `/spec <id>` | 📄 Lista specs / exibe um spec |
| `/spec new` | ✨ Cria novo spec (wizard) |
| `/agents` · `/agent create` · `/agent delete <n>` | 🤖 Lista / cria / remove agents |
| `/loop <tarefa> [flags]` | 🔁 Modo autônomo (roda sozinho até concluir/estourar orçamento) |
| `/loop-skill list` · `/loop-skill run <n>` | ♻️ Lista / roda uma loop-skill manualmente |
| `/dispatch` · `/ops` | 🧩 Despacho de tarefas do kanban / operações |
| `/thumbsup` · `/thumbsdown` | 👍👎 Avalia a última resposta (vira sinal de qualidade na memória) |
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

## 💬 bauer gateway — canais de chat (Telegram, Discord, Slack…)

O **Bauer Gateway** conecta o agent a canais de chat: você conversa com o Bauer
pelo Telegram, Discord ou Slack, com sessão persistente por chat, e o agent
pode enviar notificações a canais via tool `channel_send`.

### 🚀 Setup em 3 passos

```bash
bauer gateway init            # wizard: token, validação live, allowlist, .env
bauer gateway start           # sobe os canais habilitados + outbox (foreground)
bauer gateway start -b        # mesmo, mas em BACKGROUND (libera o terminal)
bauer gateway status          # canais, tokens, allowlists, outbox
bauer gateway stop            # encerra o gateway (e bridges antigos órfãos)
```

> `bauer gateway start -b` roda destacado, com log em `workspace/.bauer_gateway/gateway.log`. Para rodar como serviço do sistema (auto-start no boot), use `bauer gateway service install`.

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

### 💼 Slack

Via **Socket Mode** — sem URL pública/ngrok, funciona atrás de NAT/firewall.

1. [api.slack.com/apps](https://api.slack.com/apps) → Create New App.
2. **Socket Mode** → habilite → gera o App-Level Token (`xapp-…`, escopo
   `connections:write`).
3. **OAuth & Permissions** → Bot Token Scopes: `chat:write`, `im:history`,
   `im:read`, `channels:history`, `app_mentions:read` → Install to Workspace
   gera o Bot Token (`xoxb-…`).
4. **Event Subscriptions** → habilite → inscreva `message.im` e `app_mention`.
5. `bauer gateway init` → cole os dois tokens e seu user id.
6. Requer extra: `pip install 'bauer-agent[gateway]'` (websockets).

Em canais o bot responde só quando **mencionado** (`mention_only: true`); DMs
respondem sempre. Allowlists de usuário/canal no `config.yaml`.

### ⚙️ Config (config.yaml)

```yaml
telegram:
  enabled: true
  allowed_users: [123456789]    # vazio = NEGA todo mundo (seguro por default)
discord:
  enabled: true
  allowed_users: ["111222333444555666"]
  mention_only: true
slack:
  enabled: true
  allowed_users: ["U0123456789"]
  mention_only: true
gateway:
  outbox_drain_interval_s: 15   # frequência de entrega do outbox
```

Tokens ficam no `.env` (`TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`,
`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`) — nunca no config.yaml em produção.

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
| 🧠 **Cerebras** | `CEREBRAS_API_KEY` | Inferência ultra-rápida; tier gratuito (`cloud.cerebras.ai`) |
| 🐙 **GitHub Models** | `GITHUB_TOKEN` | GPT-4o, Llama via GitHub Marketplace |

### 🔐 Assinatura (usa conta ChatGPT, sem créditos de API)

| Provider | Auth | Notas |
|---|---|---|
| 🟢 **ChatGPT (browser)** | Login OAuth | Usa sua assinatura **ChatGPT Plus/Pro** via backend Responses (igual ao Codex CLI). **Experimental.** |

```bash
bauer model           # escolha "ChatGPT (browser)" → abre o browser p/ login
# ou:
bauer auth login -p openai
```

> ⚠️ **Experimental**: depende do backend do ChatGPT (`chatgpt.com/backend-api/codex`), não da API pública. Requer assinatura ChatGPT ativa. Diferente da `OpenAI API Key` (abaixo), que usa créditos de API pagos. Se o backend recusar, use uma das opções gratuitas (Groq, OpenCode) ou a API key.

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

O agente tem **~75 tools**. As principais, por categoria:

### 📁 Arquivo & código
| Tool | Descrição |
|---|---|
| `list_dir` · `read_file` · `write_file` · `append_file` · `patch` | 📂 Ler/escrever/aplicar patch |
| `create_dir` · `delete_file` · `move_file` · `diff_files` | 📦 Gerenciar arquivos |
| `search_text` · `glob_files` · `regex_search` | 🔎 Buscar por texto/padrão/regex |
| `code_symbols` · `find_definition` · `find_usages` · `get_imports` | 🧬 Navegação de código |
| `lsp_*` (hover, definitions, references, rename, diagnostics, format…) | 🛰️ Language Server (quando disponível) |

### ⚙️ Execução & utilidade
| Tool | Descrição |
|---|---|
| `run_command` | 💻 Comando shell (allowlist + denylist + safe_mode) |
| `execute_code` · `process` | 🐍 Roda código / gerencia processos em background |
| `calculate` · `datetime_now` · `json_query` · `encode_decode` · `todo` | 🧮 Utilidades |

### 🌐 Web & navegador
| Tool | Descrição |
|---|---|
| `web_search` | 🔍 Busca na web — **default Wikipedia (sem chave)**; geral com extra `[web]` |
| `web_fetch` · `http_request` | 📥 GET de URL (fallback p/ browser em SPA) / HTTP genérico |
| `browser_*` (navigate, click, type, snapshot, vision…) | 🕹️ Navegador real via Playwright |

### 🏭 App Factory, agents & skills
| Tool | Descrição |
|---|---|
| `app_factory_init` · `app_factory_status` · `app_factory_score` · `verify_app` | 🏭 Governança spec-driven + verificação real |
| `delegate_task` | 🧑‍🔧 Delega a um especialista (auto-seleção ou `agent_name`) |
| `skills_list` · `skill_view` · `skill_manage` | ✨ Consulta/gerencia skills |

### 📋 Kanban, memória & canais
| Tool | Descrição |
|---|---|
| `kanban_*` (create, list, show, complete, block, comment…) | 📋 Board de tarefas |
| `memory` · `session_search` | 🧠 Memória persistente + busca em sessões |
| `channel_send` · `channel_list` · `send_message` | 📤 Notifica canais (Telegram/Discord/…) |

### 🎨 Multimodal & avançado
| Tool | Descrição |
|---|---|
| `vision_analyze` · `video_analyze` · `image_generate` | 🖼️ Visão / geração de imagem |
| `transcribe_audio` · `text_to_speech` | 🎙️ Áudio ↔ texto |
| `clarify` · `cronjob` · `mcp_call` · `mixture_of_agents` | 🔧 Pergunta ao usuário / agenda / MCP / multi-modelo |

---

## 🎛️ Toggles de comportamento (config.yaml)

O Bauer tem defaults "agressivos mas seguros". Ajuste em `agent:` / `tools:`:

| Chave | Default | O que faz |
|---|---|---|
| `agent.minimal_code_mode` | `true` | Escada "código mínimo" (prefere reuso/stdlib a abstração nova) |
| `agent.specialist_delegation` | `true` | Injeta os especialistas e permite `delegate_task` |
| `agent.planning_checkpoint` | `true` | Checkpoint R/D/C ao terminar o planejamento da App Factory |
| `agent.skill_auto_inject` | `true` | Injeta a skill relevante no turno automaticamente |
| `tools.safe_mode` | `true` | Bloqueia comandos de risco médio sem `confirm` |
| `tools.max_tool_turns` | `150` | Teto de tool calls por turno |
| `tools.extra_allowed_commands` | `[]` | Libera comandos além da allowlist (ex.: `[docker, kubectl]`) |

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

### Setup em 3 comandos (recomendado — usa uv)

```bash
pip install uv          # instala o gerenciador de pacotes
uv sync --all-extras    # instala todas as dependências (incluindo dev)
uv run pytest           # roda a suite
```

> **Windows — conflito com `bauer.exe` em uso:** se `uv sync` falhar por permissão no executável,
> pare o processo antes: `taskkill /f /im bauer.exe` (cmd) ou `Stop-Process -Name bauer -Force` (PowerShell).
> Alternativa: use `uv run bauer` em vez de instalar o executável globalmente.

### Comandos úteis

```bash
# Cobertura
uv run pytest --cov=bauer --cov-report=term-missing

# Verificar tempo dos testes mais lentos
uv run pytest --durations=10 -q

# Lint crítico (mesmo check que bloqueia o CI)
uv run ruff check bauer/ --select E9,F63,F7,F82

# Lint completo (informativo)
uv run ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303

# Diagnóstico completo
bauer doctor
bauer doctor --providers   # testa conectividade de todos os providers
```

### Setup alternativo (sem uv)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

---

## 💡 Princípio do projeto

> Subir sem dor é mais importante que ter muitas features.

Ordem: confiável → adaptativo → aprendiz → especializado. 🚀

# Bauer Agent

Runtime adaptativo para LLMs locais e cloud.

> Hermes é rígido. Bauer é adaptativo.
> Roda com o que tem, ajusta o que precisar, avisa claramente.

---

## Índice

- [Instalação](#instalação)
- [Configuração](#configuração)
- [bauer agent](#bauer-agent)
- [bauer serve](#bauer-serve)
- [bauer gateway](#bauer-gateway)
- [Providers suportados](#providers-suportados)
- [Tools disponíveis](#tools-disponíveis)
- [Docker](#docker)
- [Desenvolvimento](#desenvolvimento)

---

## Instalação

### Linux (Debian/Ubuntu)

```bash
# 1. Dependências do sistema
sudo apt install python3-full python3-pip -y

# 2. Clonar o repositório
git clone https://github.com/henriqueferraz86/bauer-agent.git
cd bauer-agent

# 3. Criar e ativar o ambiente virtual
python3 -m venv .venv
source .venv/bin/activate

# 4. Instalar o Bauer (com servidor HTTP)
pip install -e ".[server]"

# 5. Verificar instalação
bauer doctor
```

### Windows

```powershell
# 1. Clonar o repositório
git clone https://github.com/henriqueferraz86/bauer-agent.git
cd bauer-agent

# 2. Criar e ativar o ambiente virtual
python -m venv .venv
.venv\Scripts\activate

# 3. Instalar o Bauer (com servidor HTTP)
pip install -e ".[server]"

# 4. Verificar instalação
bauer doctor
```

> **Nota Windows**: ao digitar API keys no seletor de modelos, o campo está mascarado — o texto não aparece enquanto você digita (comportamento normal do `getpass`).

---

## Configuração

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

## bauer agent

O **bauer agent** é o modo interativo principal — um assistente com memória, tools e suporte a agents especializados.

### Chat básico

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

### Agents especializados

Agents são perfis com system prompt, ferramentas e modelo próprios, definidos em `agents.yaml`.

```bash
# Listar agents disponíveis
bauer agent list

# Criar novo agent (wizard interativo)
bauer agent create

# Iniciar agent
bauer agent run <nome>

# Exemplo:
bauer agent run python
bauer agent run data-analyst
bauer agent run henrique-ferraz
```

Cada agent tem seu próprio histórico de sessão (`agent-<nome>.jsonl`) — retoma automaticamente de onde parou.

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

### Empresas (multi-tenant local)

Cada empresa tem workspace, memória e sessions isoladas:

```bash
bauer company create      # wizard de criação
bauer company list        # lista empresas
bauer company use <slug>  # ativa empresa
bauer company info <slug> # detalhes
```

Com empresa ativa, `bauer chat` e `bauer agent run` usam automaticamente o workspace isolado dela.

### Orquestrador multi-passo

```bash
bauer orchestrate run "pesquise sobre Python 3.13 e crie um resumo"
bauer orchestrate run "analise os arquivos do projeto e gere relatório" --interactive
```

O orquestrador planeja a tarefa em passos com DAG de dependências, executa passos independentes em paralelo e salva progresso em disco.

### Comandos dentro da sessão

| Comando | Descrição |
|---|---|
| `/model` | Troca provider/modelo ao vivo (sem reiniciar) |
| `/status` | Tokens usados, budget e modelo atual |
| `/clear` | Limpa histórico da sessão |
| `/sessions` | Lista sessões salvas |
| `/memory` | Lista arquivos de memória do agent |
| `/memory search <query>` | Busca semântica na memória |
| `/memory note <texto>` | Adiciona nota à memória |
| `/project` | Exibe PROJECT.md e resumo de tarefas |
| `/kanban` | Exibe board de tarefas (TASKS.md) |
| `/task add <título>` | Adiciona tarefa ao Kanban |
| `/task start <id>` | Marca tarefa como em andamento |
| `/task done <id>` | Conclui tarefa |
| `/spec list` | Lista specs do projeto |
| `/spec new` | Cria novo spec (wizard) |
| `/agents` | Lista agents disponíveis |
| `/exit` | Encerra a sessão |

---

## bauer serve

O **bauer serve** expõe o Bauer como uma API HTTP REST + Web UI, permitindo integração com outras aplicações, automações e uso remoto.

### Iniciar o servidor

```bash
bauer serve
# Padrão: http://localhost:7770

bauer serve --port 8080
bauer serve --host 0.0.0.0 --port 7770   # aceita conexões externas
```

A Web UI fica disponível em `http://localhost:7770` (interface de chat no browser).

### Autenticação

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

### Rate limiting

```yaml
serve:
  rate_limit:
    requests: 60    # requisições por janela
    window_s: 60    # janela em segundos
```

Retorna `429 Too Many Requests` com header `Retry-After` quando excedido. Desative com `requests: 0`.

### Endpoints

#### Públicos (sem auth)

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/health` | Liveness check — `{"status": "ok", "model": "..."}` |
| `GET` | `/status` | Modelo, contexto, tools disponíveis |
| `GET` | `/tools` | Lista tools com schema |
| `GET` | `/v1/models` | Lista modelos (OpenAI-compat) |
| `GET` | `/metrics` | Métricas Prometheus (text/plain) |

#### Autenticados

| Método | Endpoint | Descrição |
|---|---|---|
| `POST` | `/chat` | Envia mensagem, recebe resposta completa |
| `GET` | `/stream` | Resposta em tempo real via SSE |
| `GET` | `/sessions` | Lista sessões ativas |
| `DELETE` | `/sessions/{id}` | Remove sessão |
| `POST` | `/v1/chat/completions` | OpenAI-compatible (batch ou stream) |
| `POST` | `/models/switch` | Troca modelo ao vivo |

#### Exemplos de uso

```bash
# Chat simples
curl -X POST http://localhost:7770/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sua-chave" \
  -d '{"message": "Olá!", "session_id": "minha-sessao"}'

# Streaming (SSE)
curl "http://localhost:7770/stream?message=Olá&session_id=s1" \
  -H "X-API-Key: sua-chave"

# OpenAI-compatible (compatível com qualquer cliente OpenAI)
curl -X POST http://localhost:7770/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sua-chave" \
  -d '{
    "model": "bauer",
    "messages": [{"role": "user", "content": "Olá!"}],
    "stream": true
  }'

# Métricas Prometheus
curl http://localhost:7770/metrics
```

#### Métricas Prometheus disponíveis

```
bauer_uptime_seconds          — tempo online
bauer_requests_total          — total de requisições HTTP
bauer_requests_errors_total   — erros 5xx
bauer_chat_requests_total     — chamadas ao /chat
bauer_stream_requests_total   — chamadas ao /stream
bauer_tool_calls_total        — tool calls executadas
bauer_rate_limited_total      — requisições bloqueadas por rate limit
```

### Integração com Claw3D / Virtual Office

O `bauer serve` é compatível com o protocolo OpenAI SSE. Configure no Claw3D:

```
url:         http://localhost:7770
adapterType: custom
```

O header `X-Hermes-Session-Id` é respeitado para retomada de sessão entre requisições.

---

## bauer gateway

O **bauer gateway** é uma camada WebSocket que faz bridge entre clientes WebSocket (como o Claw3D) e o `bauer serve` (HTTP).

### Arquitetura

```
Cliente WebSocket (Claw3D)
        ↕  WebSocket ws://localhost:18789
   bauer gateway
        ↕  HTTP http://localhost:7770
   bauer serve
        ↕
   LLM (Ollama / Groq / OpenAI / etc.)
```

### Iniciar

```bash
# bauer serve precisa estar rodando primeiro
bauer serve &

# Depois inicia o gateway
bauer gateway
# Padrão: ws://localhost:18789 → http://localhost:7770

bauer gateway --port 18789 --bauer-url http://localhost:7770
```

### Protocolo WebSocket (Hermes v3)

O gateway implementa o protocolo Hermes WebSocket completo:

| Evento | Direção | Descrição |
|---|---|---|
| `connect.challenge` | → cliente | Handshake inicial com challenge |
| `connect` | ← cliente | Resposta ao challenge |
| `hello-ok` | → cliente | Conexão estabelecida com capacidades |
| `chat.send` | ← cliente | Envia mensagem; inicia streaming |
| `chat.abort` | ← cliente | Cancela run em andamento |
| `chat.history` | ← cliente | Solicita histórico da sessão |
| `agents.list` | ← cliente | Lista agents disponíveis |
| `sessions.list` | ← cliente | Lista sessões |
| `sessions.reset` | ← cliente | Limpa histórico de sessão |
| `sessions.patch` | ← cliente | Atualiza metadados de sessão |
| `models.list` | ← cliente | Lista modelos disponíveis |
| `status` | ← cliente | Status do servidor |
| `config.get` | ← cliente | Configuração ativa |
| `heartbeat` | → cliente | Keepalive a cada 25s |

**Capacidades anunciadas no `hello-ok`:**

```json
{
  "protocol": 3,
  "adapterType": "bauer",
  "features": {
    "methods": ["agents.list", "sessions.list", "chat.send", "chat.abort", ...],
    "events": ["chat", "presence", "heartbeat"]
  }
}
```

### Streaming de chat

O gateway faz SSE bridge — cada chunk de texto do LLM é emitido como evento WebSocket `chat` em tempo real:

```
cliente → chat.send {message: "Olá"}
gateway → res ok {status: "started", runId: "abc123"}
gateway → event chat {type: "delta", content: "Ol"}
gateway → event chat {type: "delta", content: "á!"}
gateway → event chat {type: "final", content: "Olá! Como posso ajudar?"}
```

### Configuração de API key

```bash
bauer gateway --api-key sua-chave-secreta
```

O gateway repassa a key automaticamente para o `bauer serve` em todas as requisições.

---

## Providers suportados

| Provider | Variável de ambiente | Notas |
|---|---|---|
| **Ollama** (local) | — | Modelos locais; sem custo; requer Ollama rodando |
| **Groq** | `GROQ_API_KEY` | Rápido; tier gratuito generoso |
| **OpenAI** | `OPENAI_API_KEY` | GPT-4o, o1, etc. |
| **Anthropic** | `ANTHROPIC_API_KEY` | Claude 3.5 Sonnet, Claude 3 Opus |
| **Google Gemini** | `GEMINI_API_KEY` | Gemini 1.5 Pro/Flash |
| **Mistral** | `MISTRAL_API_KEY` | Mistral Large, Codestral |
| **DeepSeek** | `DEEPSEEK_API_KEY` | DeepSeek-V3, R1 |
| **xAI** | `XAI_API_KEY` | Grok |
| **Together AI** | `TOGETHER_API_KEY` | Llama, Qwen e outros open-source |
| **OpenRouter** | `OPENROUTER_API_KEY` | Agregador — acesso a +200 modelos |
| **Azure OpenAI** | `AZURE_OPENAI_API_KEY` | GPT via Azure |
| **GitHub Models** | `GITHUB_TOKEN` | Modelos via GitHub Marketplace |
| **GitHub Copilot** | — | Auth via Device Flow do GitHub |
| **LM Studio / vLLM** | — | Qualquer endpoint OpenAI-compatible |

---

## Tools disponíveis

### Arquivo
| Tool | Descrição |
|---|---|
| `list_dir` | Lista arquivos e diretórios |
| `read_file` | Lê conteúdo de arquivo |
| `write_file` | Escreve/sobrescreve arquivo |
| `append_file` | Adiciona conteúdo ao final |
| `create_dir` | Cria diretório |
| `delete_file` | Remove arquivo |
| `move_file` | Move ou renomeia arquivo |
| `diff_files` | Compara dois arquivos |
| `search_text` | Busca texto em arquivo |

### Busca
| Tool | Descrição |
|---|---|
| `glob_files` | Encontra arquivos por padrão glob |
| `regex_search` | Busca com regex em arquivos |

### Utilidade
| Tool | Descrição |
|---|---|
| `calculate` | Avalia expressão matemática |
| `datetime_now` | Data e hora atual |
| `json_query` | Consulta JSON com path |
| `encode_decode` | Base64, URL encoding, hash |

### Opcionais
| Tool | Descrição | Requer |
|---|---|---|
| `run_command` | Executa comando shell | config `allow_shell: true` |
| `web_search` | Busca na web | `SERPAPI_KEY` ou similar |
| `web_fetch` | Faz GET em URL | — |
| `http_request` | HTTP GET/POST genérico | — |

---

## Docker

```bash
# Sobe Bauer + Ollama no mesmo container
docker compose up -d

# Logs
docker compose logs -f

# API disponível em http://localhost:8000
# O modelo padrão (qwen2.5-coder:3b) é baixado automaticamente no primeiro boot
```

Para mudar o modelo padrão:

```yaml
# docker-compose.yml
environment:
  - BAUER_MODEL=llama3.2:3b
```

---

## Desenvolvimento

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

## Princípio do projeto

> Subir sem dor é mais importante que ter muitas features.

Ordem: confiável → adaptativo → aprendiz → especializado.

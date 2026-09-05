# 🤖 Bauer Agent

Runtime adaptativo para LLMs locais e cloud.

> Hermes é rígido. Bauer é adaptativo.
> Roda com o que tem, ajusta o que precisar, avisa claramente.

---

## Bauer Agent Runtime Beta

Bauer Agent Runtime e a camada de execucao governada do Bauer. O beta fechado reune adapter nativo, adapter Agno, Policy Engine, Skill Registry, runs, sessions, Event Bus, scheduler, dashboard, Windows Skill Pack e observability em um fluxo unico.

Use o runtime quando quiser operar agentes com historico auditavel, aprovacao para acoes sensiveis, agendamento persistente e visibilidade de eventos:

```powershell
# validar adapters disponiveis
uv run bauer runtime list
uv run bauer runtime test bauer_native
uv run --with agno --with sqlalchemy bauer runtime test agno --config config.yaml

# iniciar API/dashboard local
uv run bauer serve --config config.yaml --host 127.0.0.1 --port 8000

# abrir dashboard
# http://127.0.0.1:8000/
```

Principais comandos do beta:

| Area | Comandos |
|---|---|
| Runtime | `bauer runtime list`, `bauer runtime test agno`, `bauer runtime use bauer_native`, `bauer runtime kill-switch on/off/status` |
| Runs e sessions | `bauer runs list`, `bauer runs show <run_id>`, `bauer runs events <run_id>`, `bauer sessions list` |
| Policy e approvals | `bauer approvals list`, `bauer approvals approve <id>`, `bauer approvals deny <id>` |
| Skills | `bauer skills validate`, `bauer skills inspect <skill_id>`, `bauer skills find <capability>` |
| Scheduler | `bauer cron create`, `bauer cron list`, `bauer cron run <id>`, `bauer runtime start` (supervisor always-on: dispatcher + cron + outbox + kanban) |
| Observability | `GET /runs`, `GET /events`, `GET /audit`, dashboard local |

Roteiro de demo fechado: [docs/BETA_CLOSED.md](docs/BETA_CLOSED.md).

---

## 📋 Índice

- [⚡ Instalação](#instalação)
- [⚙️ Configuração](#configuração)
- [🧠 Modos de uso](#modos-de-uso) — chat · agent · `--local` · App Factory · /loop · especialistas · skills
- [🛡️ Governança da execução](#governança-da-execução) — Kernel · gates · `.bauer/task.yaml` · isolamento
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
Por padrão, a instalação Windows também instala captura de voz e Kokoro, e
configura automaticamente a voz portuguesa `pm_alex`; não é necessário exportar
variáveis de ambiente a cada sessão.

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

# 3. Atualizar o Bauer e as dependências instaladas (quando necessário)
bauer update

# 4. Iniciar — escolha o modo:
bauer run "faça a tarefa X"  # ★ autônomo: do início ao fim, na pasta atual
bauer agent                  # conversar (tools + memória, uso diário)
bauer serve                  # UI web (chat + modo autônomo no browser)
bauer chat                   # chat mínimo com o modelo
```

`bauer update` busca a versão mais recente da `master` e reinstala os extras
padrão (`gateway`, `voice` e `voice-kokoro`). Configurações, credenciais,
memória e modelos ficam fora do repositório e não são sobrescritos.

No chat web, o botão do microfone envia a fala para o mesmo STT do `bauer
agent` e reproduz a resposta por TTS quando o provider de voz está disponível.
O idioma padrão da transcrição é português; use `STT_LANGUAGE` vazio para
voltar à detecção automática.

> **Dica**: use `bauer model` a qualquer momento para trocar de provider/modelo. O menu exibe claramente quais são **GRÁTIS** e quais são **PAGOS**.

### ⚡ Qual comando eu uso?

| Você quer… | Comando |
|---|---|
| **Fazer uma tarefa de ponta a ponta, sem confirmar cada passo** | `bauer run "descreva a tarefa"` (na pasta do projeto) |
| Conversar/iterar com o agente (você no controle) | `bauer agent` |
| Usar pelo navegador (chat + botão autônomo) | `bauer serve` → abre a UI |
| Só conversar, sem ferramentas | `bauer chat` |

`bauer run` usa a **pasta atual** como workspace e o config **canônico**
(`~/.bauer/config.yaml`) — ele ignora qualquer `config.yaml` que exista na
pasta do projeto. Na UI web, o equivalente é digitar `/loop tarefa` no chat.

#### Limites de uma execução autônoma (`bauer run` / `/loop`)

Três guardrails, o que vier primeiro encerra: **tempo** (`loop.max_minutes`,
30 min), **nº de ferramentas** (`loop.max_tool_calls`, 120) e **custo
ESTIMADO** (`loop.max_cost_usd`, US$2). Sobrescreva por execução com
`--max-minutes` / `--max-tool-calls` / `--max-cost`.

> ⚠️ O **custo é uma estimativa** (depende dos dados de uso do provider e da
> tabela de preços; modelos de nuvem desconhecidos usam preço genérico). **Tempo
> e nº de ferramentas são os guardrails confiáveis** — não trate o custo como
> teto de fatura. Quando o provider devolve o custo real (OpenRouter, por
> exemplo), é ele que vale.
>
> **Provider local (Ollama, LM Studio) custa exatamente US$ 0** — nunca o preço
> genérico. Antes disso, um laço 100% local acumulava custo fantasma e o
> `--max-cost` abortava trabalho que não tinha gastado um centavo.

Não confunda com os outros limites do config, que têm escopos diferentes:

| Campo | Escopo |
|---|---|
| `loop.max_tool_calls` | uma execução autônoma inteira (`bauer run` / `/loop`) |
| `tools.max_tool_calls` | a sessão inteira do ToolRouter (qualquer modo) |
| `tools.max_tool_turns` | um único turno / rodada |
| `bauer budget` / `bauer autonomy` | teto de runtime/Kernel (ledger diário) — camada separada |

### 🧭 Perdido? Comece por aqui

Digite só **`bauer`** (sem nada): a tela de boas-vindas detecta seu estado e mostra o próximo passo certo — *sem config* → `bauer init`; *falta chave* → `bauer model`; *pronto* → `bauer agent`. E o próprio `bauer init` se oferece para abrir o agente na hora.

```bash
bauer          # tela de boas-vindas inteligente (por onde começar)
bauer start    # mesma tela, a qualquer momento
bauer guide    # tour rápido pelos modos (chat / agent / model / gateway)
```

### 🔧 Instalação manual a partir do código-fonte

> Esta é uma instalação para uso local a partir do source. Para contribuir ou
> reproduzir o CI, use o setup com `uv` em [Desenvolvimento](#-desenvolvimento).

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
bauer agent --local          # só modelos desta máquina (ver abaixo)
```

### 🔒 `--local` — nada sai desta máquina

`bauer agent --local` e `bauer serve --local` roteiam por tier usando
**`model.profiles_local`** e **`model.fallback_models_local`** — e **recusam
subir** se qualquer coisa (modelo padrão, fallback, um perfil) apontar para a
nuvem, dizendo exatamente qual campo está errado.

Não é "prefira o local". É uma garantia verificada na entrada: *quase local* não
é o que ninguém quer dizer ao digitar `--local`.

```yaml
model:
  provider: ollama            # o próprio model.name precisa ser local
  name: qwen3-coder:30b       # senão --local recusa

  router_enabled: true        # necessário para o roteamento por tier

  # usados no modo normal (podem ser de nuvem)
  profiles:
    fast:     { provider: openrouter, model: ... }
  fallback_models:
    - { provider: openrouter, name: ... }

  # usados com --local (obrigatoriamente ollama/lmstudio)
  profiles_local:
    fast:     { provider: ollama, model: qwen3-coder:30b }
    balanced: { provider: ollama, model: qwen3-coder:30b }
    coding:   { provider: ollama, model: qwen3-coder:30b }
    heavy:    { provider: ollama, model: gpt-oss:20b }
  fallback_models_local:
    - { provider: ollama, name: gpt-oss:20b }   # atenção: `name`, não `model`
```

São **dois conjuntos separados** de propósito: sem o par local, escolher rodar
offline obrigaria a trocar também a rede de segurança do modo normal. Com dois,
nenhum modo perde.

O que `--local` verifica antes de subir — as **três portas** para a nuvem:

| Porta | Por que importa |
|---|---|
| `model.profiles_local` | sem ele não há tier local para rotear |
| `model.name` / `model.provider` | caminhos sem roteamento por tier vão direto nele |
| fallbacks | um hiccup do Ollama mandaria o contexto para fora **sem uma linha na tela** |

> `router_enabled: false` não impede o `--local` de funcionar — a validação
> continua valendo e nada sai da máquina. O que se perde é o roteamento por
> tier: todo turno usa `model.name`.

> ⚠️ **Nem todo modelo local chama ferramenta.** Medido em 2026-07-30:
> `qwen2.5-coder:3b` e `qwen2.5-coder:14b` respondem *texto* em vez de emitir
> `tool_calls` — inúteis como tier de agente. `qwen3-coder:30b` e `gpt-oss:20b`
> funcionam. Teste o tier antes de confiar nele.

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

### 🎤 Voice — Conversa por voz (STT + TTS)

Grava áudio do microfone, transcreve com Whisper, e opcionalmente responde
falado com síntese de voz (Coqui XTTS-v2 local ou OpenAI cloud):

```bash
bauer voice listen              # grava até silêncio ou 120s, transcreve
bauer voice listen --duration 60 --threshold -35  # ajusta duração e sensibilidade

bauer voice ask                 # grava, envia ao Bauer, imprime a resposta
bauer voice ask --speak         # idem, e também fala a resposta em voz

bauer voice speak "algum texto"           # sintetiza e toca
bauer voice speak "texto" -o saida.wav    # sintetiza e salva (não toca)

bauer voice chat                # conversa contínua por voz — ouve, responde
                                 # falado, ouve de novo. Diga "sair" ou Ctrl+C.

bauer voice transcribe audio.wav   # transcreve um arquivo existente
bauer voice status                  # verifica dependências e recursos locais
bauer voice metrics                 # mostra latências dos turnos persistidos
```

Dentro de `bauer agent`, `/listen` (ou `/ouvir`) captura a pergunta, envia a
transcrição ao agente e tenta reproduzir a resposta em voz. A resposta textual
continua sendo exibida normalmente. Nesta primeira versão, a saída usa o
endpoint TTS compatível com OpenAI do cliente ativo e um player de áudio do
sistema. Quando o provider entrega streaming, o Bauer já enfileira frases e
começa o TTS antes de o LLM terminar. A interrupção durante a fala está
disponível de forma opt-in com VAD + AEC; wake word e full-duplex contínuo entram
nas próximas fases do Bauer Jarvis.

`bauer voice listen` continua sendo apenas transcrição; para fazer uma pergunta
por voz fora da sessão interativa, use `bauer voice ask`.

Pedidos falados usam o mesmo motor de ferramentas do agente digitado: pesquisa
web, leitura/escrita de arquivos e comandos passam pelo `ToolRouter`, aguardam o
resultado e só então entram no TTS. Texto intermediário, JSON de tool calls e
progresso das ferramentas não são falados. Comandos sujeitos a confirmação
continuam protegidos pelos mesmos guards do terminal.

Para uma sessão contínua ativada por palavra-chave, use `/listen wake` dentro do
agente. O padrão é `bauer`; personalize com `BAUER_WAKE_WORD=jarvis`. Fala sem a
palavra-chave é ignorada, e `bauer parar` ou `/wake stop` encerra o modo.
O modo padrão continua validando o gatilho sobre a transcrição local/cloud;
um backend acústico dedicado agora pode ser habilitado separadamente.

Para habilitar o backend acústico opcional, instale `openwakeword`, defina o
modelo e ative-o antes de iniciar o agente:

```powershell
$env:BAUER_WAKE_BACKEND="acoustic"
$env:BAUER_WAKE_MODEL="hey_jarvis"
bauer agent
```

No backend acústico, a wake word e o comando são capturados no mesmo stream,
evitando perder o início de frases como “Bauer, abra o navegador”.

O STT incremental é o caminho padrão e único da conversa por voz. Ele envia
segmentos ao Whisper enquanto a fala continua, publica parciais internamente e
encerra a captura após aproximadamente 0,8s de silêncio. O modo legado de
captura, que aguardava 5s, não é mais selecionado pelo agente.

Por padrão, o `/listen` usa `openai/whisper-large-v3-turbo` via OpenRouter e
exige `OPENROUTER_API_KEY`. O preço publicado é US$ 0,000003 por segundo:
aproximadamente US$ 0,0108 por hora, US$ 1,08 por 100 horas e US$ 10,80 por
1.000 horas. Se a chave não estiver configurada, o próprio `/listen` mostra a
instrução de configuração e essa tabela antes de abrir o microfone.

Groq, OpenAI e faster-whisper continuam disponíveis como alternativas
explícitas com `STT_PROVIDER=groq`, `STT_PROVIDER=openai` ou
`STT_PROVIDER=local`. `STT_PROVIDER=auto` mantém a cadeia de fallback, mas não é
o padrão da instalação.

O TTS da conversa também usa streaming por frases automaticamente; não é
necessário definir `BAUER_VOICE_STREAMING=1`. `BAUER_VOICE_BARGE_IN=1` continua
disponível separadamente para habilitar interrupção da fala pelo usuário.

Se o modelo, a biblioteca ou o dispositivo não estiverem disponíveis, o Bauer
recua automaticamente para a validação pela transcrição.

**Setup:**
  - **Captura + STT**: `uv sync --extra voice` (sounddevice, numpy, soundfile,
  faster-whisper). O padrão cloud é OpenRouter:
  - OpenRouter: `OPENROUTER_API_KEY` — `openai/whisper-large-v3-turbo`
  - Local offline: `STT_PROVIDER=local`
  - Groq: `STT_PROVIDER=groq` + `GROQ_API_KEY`
  - OpenAI: `STT_PROVIDER=openai` + `OPENAI_API_KEY`
- **TTS (resposta falada)**: escolha uma:
  - Local offline (recomendado, sem key, fala pt nativamente): `uv sync --extra
    voice-tts` **+ PyTorch/Torchaudio/Torchcodec à parte** — a lib não os
    instala junto de propósito (deixa você escolher a build certa em vez do
    resolver do pip puxar a errada; `torchcodec` é exigido a partir do torch
    2.9, novo backend de I/O de áudio do torchaudio):
    - CPU (funciona em qualquer máquina): `pip install torch torchaudio
      torchcodec --index-url https://download.pytorch.org/whl/cpu`
    - GPU NVIDIA/CUDA: veja https://pytorch.org/get-started/locally/
      (+ `torchcodec` da mesma build)

    Pesos do XTTS-v2 (~1.9GB) baixam do Hugging Face na 1ª execução, sob a
    Coqui Public Model License — uso não-comercial. Testado de ponta a ponta
    (download + síntese real) em Ubuntu/CPU em 2026-08-30.
  - OpenAI: `OPENAI_API_KEY` (mesma key do STT cloud serve para os dois)

`TTS_PROVIDER=auto` (default) tenta local primeiro, cai para OpenAI se
`coqui-tts` não estiver instalado. Com `--extra voice` + `--extra voice-tts`
e nenhuma env configurada, `bauer voice chat` roda 100% offline.

Para usar uma voz autorizada como referência no XTTS-v2, configure-a uma vez:

```powershell
bauer voice xtts-setup "C:\Users\henri\Downloads\jarvis18s\jarvis18s-reference.wav"
```

O Bauer copia o WAV para `$BAUER_HOME/voices/jarvis18s-reference.wav`, ativa
`BAUER_TTS_PROVIDER=local` e salva a configuração no perfil do usuário. Assim,
`/listen`, `bauer voice chat` e o streaming por frases usam a mesma voz após
reinicializações e atualizações. O áudio de referência deve ser seu ou ter
autorização de uso; ele não é incluído no repositório.

No Windows, quando o TorchCodec pedir DLLs de áudio, instale o build
**compartilhado** do FFmpeg. O Bauer detecta automaticamente a instalação do
WinGet; builds apenas estáticos não são suficientes para o XTTS-v2.

Para um TTS neural local mais leve, use o Kokoro-82M via ONNX Runtime:

```powershell
uv sync --extra voice --extra voice-kokoro
bauer voice kokoro-download
bauer voice speak "Olá, Henrique. Todos os sistemas estão operacionais."
```

O Kokoro inclui vozes brasileiras (`pf_dora`, `pm_alex`, `pm_santa`) e vozes
inglesas britânicas. Os pesos do modelo são Apache 2.0; o runtime ONNX é MIT.
`pm_alex` é o padrão quando `BAUER_TTS_LANGUAGE=pt-BR` (ou quando nenhum
idioma é informado). O download fica em `$BAUER_HOME/models/kokoro`, fora do
repositório.

Toggle: `tools.voice_input_enabled` no config (default `false` — opt-in).

Para a saída de voz, `BAUER_TTS_PROVIDER=auto` usa SAPI local no Windows e
recorre ao endpoint compatível com OpenAI quando necessário. Use
`BAUER_TTS_PROVIDER=local` para exigir voz local ou `openai` para ignorá-la.

Para um perfil inspirado no Jarvis, com timbre remoto mais grave e ritmo mais
pausado, use:

```powershell
$env:BAUER_TTS_PROFILE="jarvis"
$env:BAUER_TTS_PROVIDER="openai"
bauer agent
```

O perfil seleciona `onyx` quando o endpoint remoto oferece essa voz e reduz a
velocidade do SAPI local. Para escolher uma voz instalada no Windows, defina
`BAUER_TTS_LOCAL_VOICE` com o nome exato da voz e, se necessário, ajuste
`BAUER_TTS_RATE` entre `-10` e `10`. O resultado é uma voz inspirada no estilo
JARVIS, não uma cópia da voz original.

O VAD pode monitorar o microfone durante todo o playback com
`BAUER_VOICE_BARGE_IN=1`. O AEC usa como referência o WAV que está sendo
reproduzido e cancela esse sinal antes do VAD; o monitor permanece ativo entre
as frases TTS e o recurso continua opt-in porque o resultado depende do
posicionamento e do dispositivo de áudio.

Cada turno de voz registra latências de captura/STT, primeiro delta do LLM,
síntese e playback. Com o EventBus do runtime ativo, o resultado é publicado
no evento `voice.turn.completed`.

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

## 🛡️ Governança da execução

Um agente autônomo entrega testes verdes e um sistema que não sobe. Já
aconteceu aqui: um projeto gerado de ponta a ponta pelo Bauer passou em **32
testes** carregando um stub literal (`for f in files: pass`), um worker em crash
loop desde o primeiro segundo e um frontend devolvendo 502 em todo o `/api/`.
Os três têm a mesma forma — **o código passa, o sistema não sobe** — e teste de
unidade não vê nenhum deles.

A camada de governança existe para isso: **o agente não pode declarar sucesso
sem validação**.

### O Kernel — ligado por padrão

Toda execução passa pelo `BauerKernel`: estados persistidos, `policy_check`
antes de qualquer chamada ao LLM, kill-switch central, eventos auditáveis e —
com `evaluator_enabled` — quality gates antes de concluir.

```yaml
kernel:
  enabled: true            # default; desligar exige escrever false
  evaluator_enabled: true  # roda os gates antes de declarar completed
```

Estados de um run: `created → planning → policy_check → queued → running →
evaluating → completed`. Se um gate reprova, o Kernel devolve o veredito como
feedback e replaneja em vez de fechar o run — até `kernel.max_replans` vezes
(default 1); esgotado, o run falha com o motivo do gate.

```bash
bauer runs list                 # runs recentes e seus estados
bauer runs show <run_id>        # o run inteiro
bauer runs events <run_id>      # a trilha de eventos
bauer approvals list            # o que está esperando você
```

### Os gates

| Gate | Reprova quando |
|---|---|
| `NonEmptyOutput` | o run termina sem produzir nada |
| `NoTraceback` | a saída final carrega um traceback não tratado |
| `Tests` | a suíte do projeto falha — **só dispara se o run mudou arquivo**, e só o passo `test`, com timeout obrigatório |
| `Baseline` | apareceu falha **nova**; teste que já estava vermelho é reportado, não bloqueia (ratchet — quando o conjunto encolhe, aperta sozinho) |
| `Scope` | o run alterou arquivo fora de `scope.allowed` / dentro de `scope.forbidden` |
| `Secrets` | um segredo entrou nas linhas **adicionadas** do diff (reporta nome e 8 primeiros chars, nunca o valor) |
| `Diff` | havia contrato de tarefa e **nada mudou** — o falso-sucesso mais barato de produzir |
| `Acceptance` | os `validation.commands` do contrato não passam |

Dois deles merecem destaque:

- **`Acceptance`** é o que pegaria os três defeitos do começo desta seção: ele
  roda comandos de verdade (`docker compose up`, `curl /api/health`), não
  asserts.
- **`Diff`** ataca o caso que nenhum outro gate vê: o agente escreve *"pronto,
  implementei"* e o repositório está idêntico. O de testes não roda (não houve
  mudança), o de escopo passa (não violou nada), e os gates de texto olham
  justamente a resposta que mentiu.

Quando um gate reprova, o motivo vira **`replan_feedback`**: o laço recebe a
cauda do output e tenta corrigir. É o ciclo que transforma *"o agente disse que
terminou"* em *"os testes passaram"*.

### `.bauer/task.yaml` — o contrato da tarefa

Opcional. Sem contrato, tudo funciona como antes; com contrato, o run ganha
perímetro e critério de pronto.

```yaml
objective: "corrigir o cálculo de tamanho dos arquivos"

scope:
  allowed:   ["backend/app/services/", "tests/"]
  forbidden: [".bauer/", ".github/"]

acceptance_criteria:
  - "a tela mostra o tamanho real, não 0 GB"

validation:
  commands:
    - "docker compose up -d && sleep 20"
    - "curl -fsS http://localhost:3000/api/health"
    - "pytest tests/ -q"
  timeout_seconds: 600
  selection: related     # related | full

isolation: worktree      # none | worktree | container
risk_level: high         # low | medium | high | critical
requires_approval: true  # o Kernel PARA e espera você
```

Dois detalhes que não são acidentais:

- O `AcceptanceGate` usa o **snapshot** lido antes do run e nunca relê do disco.
  Um agente que edita o próprio `task.yaml` no meio do caminho não escreve o
  critério que vai julgá-lo. `scope.forbidden` incluir `.bauer/` é defesa em
  profundidade, não a defesa principal.
- `risk_level: high` ou `critical` faz o Kernel entrar em `waiting_approval` e
  **parar**, esperando `bauer approvals approve`.

### Isolamento — o que está e o que não está contido

| Nível | Estado |
|---|---|
| `none` | trabalha na pasta atual |
| `worktree` | ✅ git worktree por run — o trabalho é publicado no fim, ou preservado se falhar |
| `container` | ❌ **não implementado** |
| aprovação humana | ✅ `requires_approval` para o run antes de agir |

> ⚠️ **O worktree protege o histórico do git, não a máquina.** O executor de
> shell é allowlist **por binário**, não por caminho — e `python` precisa estar
> liberado para o agente rodar testes. Medido: `python -c "open('/tmp/x','w')"`
> escreve fora do workspace e `curl` sai para a rede. Se você vai deixar um run
> autônomo solto numa máquina que importa, isso é o que você precisa saber.

### Medindo

```bash
python -m evals.harness.medir     # scorecard das 11 capacidades
```

Estado em 2026-07-31: **98%**, 21 dos 22 indicadores. Detalhes, o que falta e as
lições da campanha em [`docs/harness/`](docs/harness/README.md).

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

## 📱 Integração social (Postiz)

O Bauer publica/agenda posts em redes sociais reais (Instagram, X, LinkedIn,
TikTok, YouTube, Facebook, Reddit, Pinterest, Threads, Bluesky, Mastodon…)
via [Postiz](https://postiz.com) — self-hosted (`docker compose up -d`, veja
o [docker-compose.yaml deles](https://github.com/gitroomhq/postiz-app)) ou a
versão hospedada (`api.postiz.com`).

### ⚙️ Config

```yaml
postiz:
  api_url: https://api.postiz.com   # ou http://localhost:4007/api p/ self-hosted
  api_key: ""                        # prefira POSTIZ_API_KEY no .env
```

Cada rede social precisa ser conectada **dentro da instância Postiz**
primeiro (OAuth) — o Bauer só consome a API pública dela depois disso.

### 🛠️ Tools

- `social_list_channels` — lista as contas conectadas (IDs de integração).
- `social_post` — publica ou agenda (`content`, `channels`, `media_paths?`,
  `schedule_at?`, `post_type: schedule|draft`). Ação pública e praticamente
  irreversível — passa pelo **G4 LLM Approval** (confirmação antes de
  executar).

Exemplo de conversa: *"gera uma imagem de um pôr do sol e posta no Instagram
e no X"* → o agent usa `image_generate`, depois `social_post` com o
arquivo gerado.

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
| `channel_send` · `channel_list` · `send_message` | 📤 Notifica canais (Telegram/Discord/Slack/…) |
| `social_list_channels` · `social_post` | 📱 Publica/agenda em redes sociais via [Postiz](#-integração-social-postiz) |

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
| `tools.confirm_commands` | `true` | Prompt interativo para comando fora da allowlist (aprende) |
| `tools.voice_input_enabled` | `false` | Ativa `bauer voice listen` (requer sounddevice + faster-whisper) |

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

### Setup em 3 comandos (contribuição — igual ao CI)

```bash
pip install uv                                  # instala o gerenciador de pacotes
uv sync --frozen --extra dev                    # usa exatamente o uv.lock do CI
uv run pytest tests/ -q --tb=short              # roda a suite
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

> Para contribuição, não substitua este fluxo por `pip install -e ".[dev]"`:
> ele resolve as constraints no momento da instalação, enquanto o CI usa o
> `uv.lock` versionado.

---

## 💡 Princípio do projeto

> Subir sem dor é mais importante que ter muitas features.

Ordem: confiável → adaptativo → aprendiz → especializado. 🚀

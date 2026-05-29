# Bauer Agent

> Atualizado com camada de auto adaptação, aprendizado contínuo e evolução futura com LoRA/QLoRA.


## 1. Objetivo

Criar um agente local/remoto semelhante ao Hermes Agent, mas sem os problemas principais encontrados no Hermes:

- Exigência obrigatória de 64K de contexto.
- Dependência de modelos com suporte nativo a tools.
- Alto consumo de RAM em VPS pequena.
- Divergência entre modelo configurado, modelo carregado e contexto real.
- Falta de validação clara antes de iniciar o agente.

O Bauer Agent deve ser mais flexível, leve e transparente.

---

## 2. Problemas que o Bauer Agent precisa resolver

### Problema 1: Hermes exige 64K + tools

O Hermes parte de uma premissa rígida:

```txt
Modelo precisa ter 64K de contexto e suporte a tools.
```

Na prática, isso quebra em vários cenários locais, principalmente usando Ollama em VPS com pouca RAM.

### Problema 2: Ollama carrega modelos com 32K

Mesmo que a config peça 64K, o Ollama pode carregar o modelo com contexto menor, como 32K.

O Bauer Agent precisa detectar o contexto real aplicado e não confiar cegamente no valor do `config.yaml`.

### Problema 3: Alguns modelos não suportam tools

Muitos modelos locais não têm tool calling nativo.

O Bauer Agent não pode depender disso. Ele deve ter um modo alternativo chamado **Tool Bridge**.

### Problema 4: Modelo 7B com 64K passa da RAM da VPS

Modelo 7B com contexto alto pode ultrapassar a RAM disponível.

O Bauer deve calcular um contexto seguro baseado na máquina.

### Problema 5: Config aponta para um modelo, mas runtime usa outro

O Bauer deve ter validação forte para evitar divergência entre:

- Modelo configurado.
- Modelo disponível no Ollama.
- Modelo realmente carregado.
- Contexto solicitado.
- Contexto aplicado.

---

## 3. Princípio central

O Hermes funciona assim:

```txt
Sem 64K + tools, não roda.
```

O Bauer Agent deve funcionar assim:

```txt
Roda com o que tem, ajusta o que precisar e avisa claramente.
```

O agente deve adaptar o comportamento conforme o ambiente:

```txt
Modelo pequeno = agente simples.
Modelo médio = agente com memória resumida.
Modelo forte = agente com contexto maior e tools nativas.
Falhas anteriores = ajustes melhores na próxima execução.
```

---

## 4. Arquitetura sugerida

```txt
bauer-agent/
├── config.yaml
├── models.yaml
├── tools.yaml
├── bauer/
│   ├── main.py
│   ├── cli.py
│   ├── runtime.py
│   ├── config_loader.py
│   ├── model_registry.py
│   ├── ollama_client.py
│   ├── tool_router.py
│   ├── memory_manager.py
│   ├── context_manager.py
│   ├── learning_engine.py
│   ├── feedback_store.py
│   ├── performance_tracker.py
│   ├── skill_registry.py
│   ├── self_tuner.py
│   └── preflight.py
├── workspace/
│   ├── TASKS.md
│   ├── DECISIONS.md
│   ├── MEMORY.md
│   └── files/
├── memory/
│   ├── summaries/
│   ├── sessions/
│   ├── MODEL_EXPERIENCE.md
│   ├── FAILED_ATTEMPTS.md
│   ├── USER_PREFERENCES.md
│   ├── SKILLS_LEARNED.md
│   ├── RUNTIME_LESSONS.md
│   └── vector_store/
├── adapters/
│   ├── lora/
│   └── qlora/
├── training/
│   ├── datasets/
│   ├── recipes/
│   └── evals/
└── logs/
```

---

## 5. Componentes principais

### 5.1 Preflight obrigatório

Antes de iniciar o agente, o Bauer deve rodar uma checagem:

```bash
bauer doctor
```

Essa checagem deve validar:

- Ollama está ativo?
- Modelo existe?
- Modelo configurado é o mesmo que será usado?
- Contexto configurado cabe na RAM?
- Modelo suporta tools?
- Se não suporta tools, o modo compatível será usado?
- Porta está livre?
- Config está válida?

Exemplo de saída:

```txt
Bauer Agent Doctor

Ollama: OK
Modelo configurado: qwen2.5-coder:7b
Modelo encontrado: OK
Contexto solicitado: 64000
Contexto seguro para RAM: 32000
Modo aplicado: 32000
Tools nativas: NÃO
Tool mode: bridge/json
Status: OK com ajustes
```

---

## 6. Contexto adaptativo

O Bauer não deve exigir 64K fixo.

Regra:

```txt
contexto_final = menor valor entre:
- contexto pedido na config
- contexto real suportado pelo modelo
- contexto seguro baseado na RAM
```

Exemplo de configuração:

```yaml
model:
  provider: ollama
  name: qwen2.5-coder:7b
  requested_context: 64000
  minimum_context: 8192
  auto_downgrade_context: true
```

Se a VPS só aguentar 32K, o Bauer deve reduzir automaticamente:

```txt
Contexto solicitado: 64000
Contexto aplicado: 32768
Motivo: limite seguro de RAM
```

O agente não deve quebrar por causa disso.

---

## 7. Memória para compensar contexto menor

Em vez de depender de 64K, o Bauer deve usar memória em camadas:

```txt
Contexto curto: mensagem atual + resumo.
Contexto médio: resumo + últimos arquivos + decisões.
Contexto longo: RAG + memória vetorial + histórico resumido.
```

Estrutura sugerida:

```txt
memory/
├── session_summary.md
├── project_memory.md
├── decisions.md
├── tasks.md
└── vector_store/
```

Regra de resumo automático:

```txt
Se histórico passar de 24K tokens:
- resumir mensagens antigas
- salvar decisões
- salvar tarefas
- manter apenas contexto relevante
```

---

## 8. Tools sem depender do modelo

Esse é um dos pontos mais importantes do Bauer Agent.

Alguns modelos locais não suportam tool calling nativo. Por isso, o Bauer deve ter dois modos.

---

### 8.1 Modo Native Tools

Usado quando o modelo suporta tools de verdade.

Exemplos:

- Modelos cloud com suporte a tool calling.
- Modelos locais compatíveis.
- Modelos via APIs externas.

Fluxo:

```txt
Usuário pede ação
Modelo chama tool nativamente
Bauer executa tool
Resultado volta para o modelo
```

---

### 8.2 Modo Tool Bridge

Usado quando o modelo não suporta tools nativas.

O modelo responde em JSON controlado:

```json
{
  "action": "run_command",
  "args": {
    "command": "ls -la"
  }
}
```

O Bauer interpreta, valida e executa fora do modelo.

Fluxo:

```txt
Usuário pede ação
Modelo escreve intenção em JSON
Bauer valida a intenção
Bauer executa a ferramenta
Bauer devolve o resultado ao modelo
```

Isso permite usar modelos simples sem quebrar.

---

## 9. Registro de modelos

Criar um arquivo `models.yaml`.

Exemplo:

```yaml
models:
  qwen2.5-coder:3b:
    provider: ollama
    max_context_safe: 32768
    supports_tools: false
    ram_profile: low
    recommended_for:
      - code_simple
      - shell
      - automation

  qwen2.5-coder:7b:
    provider: ollama
    max_context_safe: 32768
    supports_tools: false
    ram_profile: medium
    recommended_for:
      - coding
      - agents
      - refactor

  llama3.1:8b:
    provider: ollama
    max_context_safe: 32768
    supports_tools: partial
    ram_profile: medium

  cloud-gpt:
    provider: openai
    max_context_safe: 128000
    supports_tools: true
    ram_profile: external
```

O Bauer não deve adivinhar. Ele deve ler esse registro e validar o ambiente.

---

## 10. Config principal

Arquivo `config.yaml`:

```yaml
agent:
  name: Bauer Agent
  mode: auto
  workspace: ./workspace

model:
  provider: ollama
  name: qwen2.5-coder:7b
  requested_context: 64000
  minimum_context: 8192
  auto_downgrade_context: true

runtime:
  ram_limit_mb: 4096
  max_parallel_tasks: 1
  safe_mode: true

tools:
  mode: auto
  allow_shell: true
  allow_filesystem: true
  allow_network: false
  require_confirmation_for_dangerous_commands: true

memory:
  enabled: true
  strategy: summary_plus_recent
  summarize_after_tokens: 24000
  vector_store: false

learning:
  enabled: true
  mode: conservative
  store_model_experience: true
  store_failed_attempts: true
  auto_apply_lessons: true
  require_explanation: true
  allow_auto_skill_creation: false

logging:
  level: info
  file: ./logs/bauer.log
```

---

## 11. Modos de execução

### 11.1 Perfil Low

Para VPS fraca.

```txt
Contexto: 8K a 16K
Modelo: 3B
Tools: bridge
Memória: resumo
Paralelismo: 1
```

Comando:

```bash
bauer run --profile low
```

---

### 11.2 Perfil Medium

Para VPS com mais folga.

```txt
Contexto: 16K a 32K
Modelo: 7B
Tools: bridge ou native
Memória: resumo + arquivos
Paralelismo: 1
```

Comando:

```bash
bauer run --profile medium
```

---

### 11.3 Perfil High

Para máquina local forte ou cloud.

```txt
Contexto: 64K+
Modelo: maior
Tools: native
Memória: RAG
Paralelismo: 2+
```

Comando:

```bash
bauer run --profile high
```

---

## 12. CLI do Bauer Agent

Comandos principais:

```bash
bauer doctor
bauer run
bauer chat
bauer models list
bauer models test qwen2.5-coder:7b
bauer config show
bauer config validate
bauer memory summarize
bauer learning show
bauer learning explain
bauer learning reset
bauer learning forget-model qwen2.5-coder:7b
bauer tools list
bauer logs follow
```

Comando para testar modelo:

```bash
bauer models test qwen2.5-coder:7b
```

Saída esperada:

```txt
Modelo: qwen2.5-coder:7b
Disponível no Ollama: sim
Contexto configurado: 64000
Contexto aplicado: 32768
Tools nativas: não
Modo recomendado: tool_bridge
Status: pronto
```

---

## 13. Proteções obrigatórias

O Bauer Agent deve operar em `safe_mode` por padrão.

Ele deve pedir confirmação antes de executar comandos perigosos, como ações que:

- Apagam diretórios inteiros.
- Reiniciam ou desligam a máquina.
- Formatam disco.
- Alteram permissões de forma ampla.
- Sobrescrevem arquivos críticos.

Exemplo de mensagem:

```txt
Comando perigoso detectado.
Execução bloqueada no safe_mode.
Confirmação manual necessária.
```

---

## 14. Resolver divergência entre config e runtime

O Bauer deve ter uma fonte única da verdade.

Situação errada:

```txt
config.yaml aponta para qwen2.5-coder:7b
runtime usa deepseek-coder
Ollama está com outro contexto
```

Situação correta:

```txt
config.yaml define o desejado
doctor detecta o real
runtime salva o aplicado
```

Criar arquivo gerado em runtime:

```txt
.runtime_state.json
```

Exemplo:

```json
{
  "configured_model": "qwen2.5-coder:7b",
  "active_model": "qwen2.5-coder:7b",
  "requested_context": 64000,
  "applied_context": 32768,
  "tool_mode": "bridge",
  "status": "ok"
}
```

Assim fica claro:

- O que foi configurado.
- O que foi encontrado.
- O que foi realmente aplicado.

---


## 15. Auto adaptação e aprendizado contínuo

O plano original já cobre **auto adaptação operacional**:

```txt
RAM baixa → reduz contexto.
Modelo sem tools → usa Tool Bridge.
Modelo pesado → troca para profile low/medium.
Config divergente → bloqueia ou ajusta com aviso claro.
```

Mas, para chegar mais perto da habilidade do Hermes de aprender e se adaptar com o tempo, o Bauer precisa de uma camada própria:

```txt
Adaptive Learning Engine
```

Essa camada não deve ser mágica nem escondida. Ela precisa ser controlada, auditável e reversível.

---

### 15.1 O que o Bauer deve aprender

O Bauer deve registrar experiências reais de uso:

```txt
Qual modelo funcionou melhor nesta máquina.
Qual contexto ficou estável.
Qual contexto causou erro de RAM.
Qual modelo não suporta tools.
Quais comandos falharam.
Quais ajustes corrigiram o problema.
Quais preferências o usuário repetiu.
Quais tarefas aparecem com frequência.
```

Exemplo:

```json
{
  "model": "qwen2.5-coder:7b",
  "requested_context": 64000,
  "applied_context": 32768,
  "result": "slow",
  "ram_used_mb": 5800,
  "lesson": "avoid_64k_on_low_ram_vps",
  "recommendation": "use qwen2.5-coder:3b with 16K"
}
```

---

### 15.2 Arquivos de aprendizado

Adicionar arquivos persistentes:

```txt
memory/
├── MODEL_EXPERIENCE.md
├── FAILED_ATTEMPTS.md
├── USER_PREFERENCES.md
├── SKILLS_LEARNED.md
└── RUNTIME_LESSONS.md
```

Função de cada um:

```txt
MODEL_EXPERIENCE.md  → histórico de modelos, contexto, RAM e desempenho.
FAILED_ATTEMPTS.md   → erros encontrados e correções testadas.
USER_PREFERENCES.md  → preferências técnicas do usuário.
SKILLS_LEARNED.md    → tarefas repetidas que podem virar skill.
RUNTIME_LESSONS.md   → decisões automáticas tomadas pelo Bauer.
```

---

### 15.3 Como o aprendizado influencia o runtime

Na próxima execução, o Bauer deve consultar o histórico antes de decidir.

Exemplo:

```txt
Config pede qwen2.5-coder:7b com 64K.
Histórico mostra que esse modelo travou com 64K nesta VPS.
Bauer recomenda qwen2.5-coder:3b com 16K.
Se auto_apply_lessons=true, aplica o ajuste e explica o motivo.
```

Saída esperada:

```txt
Aprendizado aplicado:
- qwen2.5-coder:7b com 64K já falhou nesta máquina.
- Última configuração estável: qwen2.5-coder:3b com 16K.
- Aplicando profile low por segurança.
```

---

### 15.4 Níveis de aprendizado

O Bauer deve ter três níveis:

```txt
learning: off
Não aprende nada. Apenas executa a config.

learning: observe
Registra experiências, mas não muda o runtime sozinho.

learning: conservative
Registra, recomenda e aplica apenas ajustes seguros.
```

No começo, o padrão deve ser:

```yaml
learning:
  enabled: true
  mode: conservative
  allow_auto_skill_creation: false
```

---

### 15.5 Criação de skills aprendidas

O Bauer pode identificar tarefas repetidas, como:

```txt
diagnosticar Ollama
validar contexto
reiniciar serviço
testar modelo
corrigir config
```

Mas no MVP ele não deve criar tools executáveis automaticamente.

Regra segura:

```txt
Primeiro registra sugestão de skill.
Depois o usuário aprova.
Só então vira skill disponível.
```

Exemplo:

```txt
Sugestão de skill detectada:
diagnose_ollama_context

Motivo:
Você executou diagnóstico de modelo/contexto 5 vezes.
```

---

### 15.6 Comandos de aprendizado

Adicionar comandos:

```bash
bauer learning show
bauer learning explain
bauer learning reset
bauer learning forget-model qwen2.5-coder:7b
bauer learning export
```

Esses comandos servem para o usuário revisar e controlar o que o Bauer aprendeu.

---

### 15.7 Regra de segurança do aprendizado

O Bauer nunca deve aprender de forma invisível.

Toda decisão automática precisa mostrar:

```txt
O que foi aprendido.
De onde veio a evidência.
Qual ajuste foi aplicado.
Como desfazer.
```

Exemplo:

```txt
Ajuste aplicado por aprendizado:
Modelo alterado de qwen2.5-coder:7b para qwen2.5-coder:3b.
Motivo: 3 falhas anteriores de RAM com 7B nesta VPS.
Para desfazer: bauer learning forget-model qwen2.5-coder:7b
```

---

## 16. MVP em etapas

## Fase 1 — Base

Criar:

```txt
config_loader.py
ollama_client.py
preflight.py
cli.py
```

Entregar:

```bash
bauer doctor
bauer config validate
bauer models list
```

Objetivo:

```txt
Detectar modelo, contexto, RAM e status do Ollama.
```

---

## Fase 2 — Chat simples

Criar:

```txt
runtime.py
context_manager.py
memory_manager.py
```

Entregar:

```bash
bauer chat
```

Objetivo:

```txt
Conversar com modelo local sem quebrar por falta de 64K.
```

---

## Fase 3 — Tool Bridge

Criar:

```txt
tool_router.py
tools.yaml
```

Entregar tools básicas:

```txt
read_file
write_file
list_dir
run_command
search_text
```

Objetivo:

```txt
Usar ferramentas mesmo com modelo sem tool calling nativo.
```

---

## Fase 4 — Agente de projeto

Criar workspace:

```txt
workspace/
├── TASKS.md
├── DECISIONS.md
├── MEMORY.md
└── files/
```

Objetivo:

```txt
Agente conseguir trabalhar em projeto real, editar arquivos e manter memória.
```

---

## Fase 5 — Perfis automáticos

Criar:

```bash
bauer run --profile low
bauer run --profile medium
bauer run --profile high
```

Objetivo:

```txt
Rodar em VPS fraca, VPS média ou máquina forte sem ajuste manual complexo.
```

---

## Fase 6 — Adaptive Learning Engine

Criar:

```txt
learning_engine.py
feedback_store.py
performance_tracker.py
skill_registry.py
self_tuner.py
```

Entregar:

```bash
bauer learning show
bauer learning explain
bauer learning reset
bauer learning forget-model qwen2.5-coder:7b
```

Objetivo:

```txt
Fazer o Bauer aprender com falhas, desempenho e preferências, sem tomar decisões invisíveis.
```

Escopo permitido nessa fase:

```txt
Aprender modelos estáveis.
Aprender contextos seguros.
Aprender falhas repetidas.
Aprender preferências técnicas.
Sugerir skills repetidas.
```

Escopo proibido nessa fase:

```txt
Criar shell tools automaticamente.
Executar comandos aprendidos sem aprovação.
Alterar config sem registrar motivo.
```

---


## Fase futura — LoRA/QLoRA Adapter

LoRA e QLoRA podem entrar como evolução do Bauer Agent, mas não como base do MVP.

Objetivo:

```txt
Criar adaptadores especializados para deixar o modelo mais obediente aos padrões do Bauer.
```

Esses adaptadores podem ajudar em:

```txt
Diagnóstico de erros Ollama/contexto/RAM.
Formato correto do Tool Bridge.
Correção de config.yaml.
Padrões de segurança.
Comandos frequentes do Bauer.
Comportamento em VPS fraca.
Classificação de erros recorrentes.
Geração de respostas mais padronizadas para doctor e preflight.
```

Exemplo de uso futuro:

```txt
Modelo base: qwen2.5-coder:7b
Adapter: bauer-lora-diagnostico
Função: melhorar diagnóstico de contexto, RAM, Ollama e config divergente.
```

Importante:

```txt
LoRA/QLoRA não substitui o doctor.
LoRA/QLoRA não substitui o runtime_state.
LoRA/QLoRA não resolve falta de RAM em inferência.
LoRA/QLoRA não cria suporte real a tools nativas se o modelo não tiver isso.
LoRA/QLoRA não corrige arquitetura ruim.
```

A função correta é refinamento:

```txt
Primeiro o Bauer precisa funcionar bem sem fine-tuning.
Depois os adapters entram para melhorar comportamento, padrão e especialização.
```

Arquivos sugeridos:

```txt
adapters/
├── lora/
│   ├── bauer-diagnostico/
│   ├── bauer-toolbridge/
│   └── bauer-configfix/
└── qlora/
    ├── recipes/
    └── checkpoints/

training/
├── datasets/
│   ├── ollama_context_errors.jsonl
│   ├── toolbridge_examples.jsonl
│   └── config_fix_examples.jsonl
├── recipes/
│   ├── qwen_lora.yaml
│   └── qwen_qlora.yaml
└── evals/
    ├── diagnostic_eval.jsonl
    └── toolbridge_eval.jsonl
```

Exemplo de dataset:

```json
{"input":"Hermes exige 64K mas Ollama subiu com 32K","output":{"diagnosis":"context_mismatch","action":"reduce_context_or_restart_ollama","safe_context":32768}}
```

Regra do projeto:

```txt
Adapters são opcionais.
Adapters só entram depois de dados reais de uso.
Adapters precisam ser avaliados antes de virar padrão.
Adapters nunca podem ser usados para esconder bug de doctor, config ou runtime.
```

Comandos futuros:

```bash
bauer adapter list
bauer adapter train --recipe qwen_lora.yaml
bauer adapter eval bauer-diagnostico
bauer adapter enable bauer-diagnostico
bauer adapter disable bauer-diagnostico
```

---

## 17. Stack recomendada

Para começar simples:

```txt
Python
Typer para CLI
Pydantic para config
HTTPX para chamar Ollama
Rich para terminal
psutil para RAM/processos
PyYAML para config
SQLite para estado local
Chroma ou FAISS depois, se quiser RAG
```

Dependências iniciais:

```bash
pip install typer rich pydantic pyyaml httpx psutil
```

---

## 18. Comportamento esperado

Quando o Bauer subir numa VPS pequena, ele não deve falhar assim:

```txt
Model requires 64K context
```

Ele deve responder assim:

```txt
O modelo não suporta 64K com segurança nesta máquina.
Aplicando contexto de 32768.
Tools nativas não detectadas.
Usando Tool Bridge.
Agente iniciado com perfil medium.
```

---

## 19. MVP recomendado: Bauer Agent v0.1

Escopo inicial:

```txt
1. bauer doctor
2. config.yaml
3. detecção do Ollama
4. teste do modelo configurado
5. cálculo de contexto seguro
6. chat simples
7. memória por resumo
8. tool bridge básico
9. aprendizado em modo observe/conservative
```

Sem interface web no começo.

Primeiro terminal.

Depois painel web.

---

## 20. Diferencial do Bauer Agent

O diferencial é simples:

```txt
Hermes é rígido.
Bauer é adaptativo.
```

O Bauer Agent deve aceitar o ambiente real e ajustar:

- Contexto.
- Modelo.
- Tools.
- Memória.
- Perfil de execução.
- Aprendizado baseado em falhas anteriores.
- Preferências técnicas do usuário.

Sem erro confuso. Sem exigir máquina grande. Sem travar por falta de tool calling nativo. Sem aprendizado invisível.

---

## 21. Providers suportados

O Bauer Agent suporta múltiplos providers, configurados em `config.yaml`:

```yaml
model:
  provider: ollama       # Ollama local (padrão)
  provider: openai       # OpenAI oficial ou endpoint OpenAI-compatible
  provider: openrouter   # OpenRouter — acessa 200+ modelos com uma chave
  provider: custom       # Alias para openai com base_url personalizado
```

### 21.1 Ollama (local)

```yaml
model:
  provider: ollama
  name: phi4-mini

ollama:
  host: http://localhost:11434
  timeout_seconds: 120
```

### 21.2 OpenAI

```yaml
model:
  provider: openai
  name: gpt-4.1-nano

openai:
  host: https://api.openai.com
  timeout_seconds: 60
  api_key: ''   # ou via OPENAI_API_KEY no .env
```

### 21.3 OpenRouter

Acessa 200+ modelos (GPT, Claude, Gemini, Llama, DeepSeek…) com uma só chave.

```yaml
model:
  provider: openrouter
  name: openai/gpt-4o-mini

openrouter:
  api_key: ''   # ou via OPENROUTER_API_KEY no .env
  timeout_seconds: 60
```

Modelos disponíveis via OpenRouter:

```txt
openai/gpt-4o-mini          — ChatGPT 4o mini, rápido e barato
openai/gpt-4o               — ChatGPT 4o, mais capaz
anthropic/claude-haiku-3-5  — Claude Haiku, rápido
google/gemini-flash-1.5     — Gemini Flash, rápido
meta-llama/llama-3.3-70b-instruct — Llama 3.3 70B, gratuito via OR
deepseek/deepseek-chat      — DeepSeek V3, gratuito via OR
```

### 21.4 Custom (endpoint OpenAI-compatible)

Para LM Studio, vLLM, Groq, ou qualquer endpoint compatível com o protocolo OpenAI:

```yaml
model:
  provider: custom
  name: llama3.1-8b

openai:
  host: http://localhost:1234   # URL do seu endpoint
  api_key: 'sua-chave'
```

---

## 22. Suporte a .env

O Bauer carrega automaticamente um arquivo `.env` localizado na mesma pasta do `config.yaml` ou no diretório de trabalho.

Prioridade de configuração:

```txt
1. Variáveis de ambiente do sistema (export / set)
2. .env (carregado automaticamente)
3. config.yaml
```

Variáveis suportadas:

```bash
OPENAI_API_KEY=sk-...
OPENROUTER_API_KEY=sk-or-...
OLLAMA_API_KEY=...           # Para Ollama remoto com proxy autenticado
BAUER_SERVE_API_KEY=...      # Para proteger o bauer serve
```

Arquivo `.env.example` está incluído no projeto com exemplos comentados.

---

## 23. Seletor interativo de modelo

O comando `bauer model` abre um seletor interativo no terminal para trocar provider e modelo sem editar o `config.yaml` manualmente:

```bash
bauer model
```

Funcionalidades:

```txt
- Mostra provider e modelo atual em painel colorido
- Lista providers disponíveis: ollama, openrouter, openai, groq, custom
- Para ollama: busca modelos instalados automaticamente via API
- Para openrouter: lista modelos populares com descrição e custo
- Solicita API Key se não encontrada no .env
- Salva a seleção no config.yaml e .env automaticamente
```

Exemplo de uso:

```txt
$ bauer model

╔═══════════════════════════════╗
║  Provider atual: openai       ║
║  Modelo atual:   gpt-4.1-nano ║
╚═══════════════════════════════╝

Escolha o provider:
  1. ollama       — local, sem custo
  2. openrouter   — 200+ modelos na nuvem
  3. openai       — ChatGPT direto
  4. groq         — Llama/Gemma ultrarrápido
  5. custom       — endpoint OpenAI-compatible

Seleção: 2

Modelo openrouter:
  1. openai/gpt-4o-mini    — rápido e barato
  2. openai/gpt-4o         — mais capaz
  3. anthropic/claude-haiku-3-5
  ...

Seleção: 1

OPENROUTER_API_KEY não encontrada. Cole sua chave:
> sk-or-...

Config salva. Chave salva em .env.
```

---

## 24. Autenticação com providers cloud

### 24.1 Comandos auth

```bash
bauer auth login [PROVIDER]    # Faz login no provider indicado
bauer auth status              # Mostra tokens salvos e validade
bauer auth logout [PROVIDER]   # Remove token (ou todos se sem arg)
bauer auth providers           # Lista providers suportados com tipo de auth
```

### 24.2 Providers suportados

```txt
openai       — OAuth browser (PKCE, abre navegador, callback em localhost:1455)
openai-api   — API Key manual
anthropic    — API Key manual
groq         — API Key manual
deepseek     — API Key manual
openrouter   — API Key manual
```

### 24.3 Login via browser (OpenAI / ChatGPT)

```bash
bauer auth login openai
```

Fluxo PKCE:

```txt
1. Gera code_verifier + code_challenge (PKCE S256)
2. Inicia servidor local em localhost:1455
3. Abre navegador em auth.openai.com/oauth/authorize
4. Usuário faz login no browser normalmente
5. OpenAI redireciona para localhost:1455/auth/callback?code=...
6. Bauer troca o code por access_token + refresh_token
7. Tenta obter API Key via troca de token (endpoint /codex/api_key)
8. Salva em ~/.bauer/auth.json com ofuscação XOR
```

O token fica armazenado em `~/.bauer/auth.json`. Ao executar `bauer chat`, o Bauer usa automaticamente o token salvo se o provider for `openai`.

### 24.4 Login via API Key

```bash
bauer auth login openrouter
# ou
bauer auth login anthropic
```

Solicita a chave no terminal, valida e salva em `~/.bauer/auth.json` + `.env`.

### 24.5 Ver status

```bash
bauer auth status
```

Saída exemplo:

```txt
┌─────────────┬────────────┬─────────────────────┐
│ Provider    │ Tipo       │ Expira em           │
├─────────────┼────────────┼─────────────────────┤
│ openai      │ oauth      │ 2026-06-27 14:32:00 │
│ openrouter  │ api_key    │ (sem expiração)     │
└─────────────┴────────────┴─────────────────────┘
```

---

## 25. Roteador inteligente de modelos (ModelRouter)

O `ModelRouter` classifica cada mensagem do usuário e redireciona para o modelo local mais adequado, em vez de enviar tudo para um único modelo.

### 25.1 Categorias de rota

```txt
direct     — saudação, conversa simples, perguntas fáceis (data, hora)
code       — pedido de código, script, debugging, programação
reasoning  — explicação complexa, matemática, lógica, análise profunda
tool       — ler/escrever/listar arquivos, shell, web search
```

### 25.2 Configuração

```yaml
router:
  enabled: true
  router_model: qwen3:0.6b          # Modelo classificador (leve)
  code_model: Impulse2000/smollm3:latest
  reasoning_model: phi4-mini:latest
  direct_model: qwen3:0.6b
```

O `router_model` é um modelo leve (0.6B) que faz apenas a classificação. Cada categoria pode usar um modelo diferente.

### 25.3 Fluxo de roteamento

```txt
Usuário escreve mensagem
       │
       ▼
  qwen3:0.6b classifica
       │
  ┌────┴────┐
  │  kind?  │
  └────┬────┘
direct │ code │ reasoning │ tool
  qwen3  smollm3  phi4-mini  phi4-mini
```

### 25.4 Fallback

Se o modelo classificador falhar, o Bauer cai em `reasoning` (phi4-mini) por segurança.

### 25.5 Ativação

O roteador é ativado automaticamente quando `router.enabled: true` no `config.yaml`. Funciona apenas com modelos Ollama locais — para providers cloud (`openai`, `openrouter`), o modelo configurado em `model.name` é sempre usado diretamente.

---

## 26. Orquestrador de agents (AgentOrchestrator)

O Orquestrador decompõe tarefas complexas em passos com grafo de dependências (DAG), executa ondas em paralelo ou sequencialmente e sintetiza os resultados em resposta coesa. Progresso é persistido em disco — `--resume` retoma de onde parou.

### 26.1 Comandos

```bash
# Executa tarefa complexa
bauer orchestrate run "sua tarefa complexa aqui"

# Modo passo-a-passo (confirma cada onda antes de executar)
bauer orchestrate run "tarefa" --interactive

# Retoma execução interrompida
bauer orchestrate run "tarefa longa" --resume

# Especifica modelos manualmente
bauer orchestrate run "tarefa" --planner qwen3:0.6b --synthesizer phi4-mini
```

### 26.2 Fluxo de execução (DAG)

```txt
Usuário fornece tarefa
        │
        ▼
  Planejamento (qwen3:0.6b)
  Decompõe em até 6 passos com depends_on
        │
        ▼
  Resolução topológica → ondas
  (passos sem dependências entre si = mesma onda)
        │
        ▼
  Por onda: execute_parallel_steps()
    se parallel_steps=False (padrão / CPU):
      executa um passo por vez (evita OOM)
    se parallel_steps=True (profile=high / GPU):
      executa todos em threads simultâneas
        │
        ▼
  Progresso salvo em .orchestrate_progress/{hash}/
        │
        ▼
  Síntese (phi4-mini)
  Combina resultados em resposta final
        │
        ▼
  .orchestrate_progress/ removido (sucesso)
```

### 26.3 Formato do plano com dependências

O planejador produz JSON com `depends_on` por passo:

```json
{
  "objective": "criar sistema de monitoramento de logs",
  "steps": [
    {"id": 1, "goal": "listar arquivos de log em /var/log",     "tools": true,  "depends_on": []},
    {"id": 2, "goal": "analisar padrões de erro nos logs",      "tools": false, "depends_on": [1]},
    {"id": 3, "goal": "gerar script Python de monitoramento",   "tools": false, "depends_on": [1]},
    {"id": 4, "goal": "resumo final combinando análise e code", "tools": false, "depends_on": [2, 3]}
  ]
}
```

Resolução topológica deste exemplo:
```txt
Onda 0: [passo 1]
Onda 1: [passo 2, passo 3]   ← paralelos (ambos dependem só do 1)
Onda 2: [passo 4]
```

Regras de `depends_on`:
- `[]` → sem dependência, pode iniciar imediatamente
- `[1]` → aguarda o passo 1 terminar
- `[2, 3]` → aguarda os passos 2 E 3 terminarem

### 26.4 Estratégia CPU vs GPU (parallel_steps)

```python
OrchestratorConfig(
    planner_model="qwen3:0.6b",    # Modelo de planejamento (leve)
    synthesizer_model="phi4-mini", # Modelo de síntese (mais capaz)
    max_steps=6,                   # Máximo de passos
    parallel_steps=False,          # False = sequencial (CPU/baixa RAM — padrão)
                                   # True  = paralelo   (GPU/profile=high)
)
```

Por que `parallel_steps=False` é o padrão:

```txt
Ollama descarrega modelo após ~5 min idle.
Se dois passos rodassem simultâneos:
  → dois modelos carregados ao mesmo tempo
  → OOM em VPS com 8–12 GB RAM

Com False: passo A termina → Ollama libera RAM → passo B inicia.
Com OLLAMA_KEEP_ALIVE=0 (Docker): libera RAM imediatamente após cada resposta.

Só use True em máquinas com GPU ou RAM abundante (profile=high).
```

### 26.5 Persistência e --resume

```txt
Progresso salvo automaticamente em:
  .orchestrate_progress/{md5(task)[:10]}/
    plan.json      ← plano completo
    step_1.json    ← resultado do passo 1
    step_2.json    ← resultado do passo 2
    ...

Se interrompido (Ctrl+C, queda de energia, etc.):
  bauer orchestrate run "mesma tarefa" --resume
  → carrega plan.json + passos concluídos
  → executa apenas os pendentes
  → remove .orchestrate_progress/ ao fim
```

### 26.6 Escalada automática dentro do bauer agent

Quando o ModelRouter classifica uma mensagem como `orchestrate`, o `bauer agent` escala automaticamente:

```txt
Usuário: "pesquise sobre Python 3.13, analise nosso projeto e gere um relatório"

bauer agent → ModelRouter → orchestrate detectado
           → _run_orchestrator_inline() executa sem sair do chat
           → resultado volta ao contexto da conversa
```

Ativação via `config.yaml`:

```yaml
router:
  enabled: true   # obrigatório para escalada automática
```

### 26.7 Saída no terminal

```txt
──────────────────── Orquestrador ────────────────────
Tarefa: criar sistema de monitoramento de logs

Plano (4 passos, 3 onda(s)) [sequencial]:
  Onda 1:
    1. listar arquivos de log em /var/log [tools]
  Onda 2:
    2. analisar padrões de erro nos logs
    3. gerar script Python de monitoramento
  Onda 3:
    4. resumo final combinando análise e code

Passo 1: listar arquivos de log em /var/log [tools]
  Modelo: phi4-mini
    → list_dir

Passo 2: analisar padrões de erro nos logs
  Modelo: phi4-mini

Passo 3: gerar script Python de monitoramento
  Modelo: smollm3

Passo 4: resumo final combinando análise e code
  Modelo: phi4-mini

Sintetizando resultados...
────────────────── Resultado Final ──────────────────
orchestrate> [resposta coesa combinando todos os resultados]
```

---

## 27. Web tools

O Bauer suporta busca na web e fetch de URLs como ferramentas do agente.

### 27.1 Ativação

```yaml
tools:
  web_enabled: true
```

### 27.2 Ferramentas disponíveis

```txt
web_search   — busca via DuckDuckGo (sem API Key)
web_fetch    — baixa e extrai texto de uma URL
```

Essas ferramentas ficam disponíveis no ToolBridge assim que `web_enabled: true` for definido. O modelo pode invocar `web_search` ou `web_fetch` via JSON como qualquer outra tool.

### 27.3 Exemplo

```txt
Usuário: "pesquise sobre o último lançamento do Python 3.13"

Agente → web_search("Python 3.13 release")
       → retorna títulos e snippets
       → modelo resume e responde
```

---

## 28. Módulos implementados

Mapa completo dos módulos atuais do Bauer Agent:

```txt
bauer/
├── cli.py                 — Interface de linha de comando (Typer)
├── config_loader.py       — Carrega e valida config.yaml (Pydantic)
├── env_loader.py          — Carrega .env e aplica variáveis à config
├── agent.py               — Loop de agente com ToolBridge e roteamento
├── chat.py                — Modo chat simples (sem tools)
├── ollama_client.py       — Cliente HTTP para Ollama (streaming)
├── openai_client.py       — Cliente HTTP para APIs OpenAI-compatible
├── model_router.py        — Classifica mensagens e roteia por modelo
├── model_switcher.py      — Seletor interativo de provider/modelo
├── orchestrator.py        — Orquestrador multi-passo (DAG→paralelo→synth)
├── auth.py                — OAuth PKCE + API Key (TokenStore, AuthManager)
├── tool_router.py         — ToolBridge: roteia chamadas de ferramentas
├── shell_runner.py        — Executa comandos shell com sandbox
├── context_manager.py     — Gerencia janela de contexto e payload
├── memory_manager.py      — Memória por resumo (Markdown)
├── preflight.py           — Doctor: valida ambiente antes de iniciar
├── runtime_state.py       — Estado aplicado em runtime (.runtime_state.json)
├── model_registry.py      — Registro de modelos (models.yaml)
├── learning_engine.py     — Adaptive Learning Engine
├── feedback_store.py      — Armazena feedback de execuções
├── performance_tracker.py — Rastreia desempenho por modelo
├── skill_registry.py      — Registro de skills aprendidas
├── self_tuner.py          — Auto-ajuste baseado em histórico
├── workspace_manager.py   — Gerencia workspace/TASKS/DECISIONS/MEMORY
├── session_store.py       — Persiste sessões de conversa
├── logging_config.py      — Configura logging para arquivo e console
├── machine_id.py          — ID único da máquina (para aprendizado)
└── server.py              — Servidor HTTP (FastAPI + SSE + Web UI)
```

---

## 29. CLI completa (estado atual)

```bash
# ── Diagnóstico e configuração ──────────────────────────────────────────────
bauer doctor                          # Valida ambiente completo → .runtime_state.json
bauer config validate                 # Valida config.yaml sem rodar doctor
bauer config show                     # Exibe config validada (dump completo)

# ── Seletor de modelo ────────────────────────────────────────────────────────
bauer model                           # Seletor interativo de provider/modelo
                                      # Salva em config.yaml e .env automaticamente

# ── Autenticação ─────────────────────────────────────────────────────────────
bauer auth login [PROVIDER]           # Login — OAuth browser (openai) ou API Key
bauer auth status                     # Tokens salvos com data de expiração
bauer auth logout [PROVIDER]          # Remove token (sem arg = todos)
bauer auth providers                  # Lista providers com tipo de auth

# ── Chat e agente ────────────────────────────────────────────────────────────
bauer chat                            # Chat simples (sem tools)
bauer agent                           # Agente com ToolBridge, roteamento e memória
bauer agent --model phi4-mini         # Sobrescreve modelo da config
bauer agent --pick                    # Lista modelos instalados para escolher
bauer agent --profile high            # Perfil explícito (low/medium/high)

# ── Orquestrador ─────────────────────────────────────────────────────────────
bauer orchestrate run "TAREFA"        # Executa tarefa em múltiplos passos (DAG)
bauer orchestrate run "TAREFA" -i     # Modo interativo (confirma cada onda)
bauer orchestrate run "TAREFA" -r     # Retoma execução salva (--resume)
bauer orchestrate run "TAREFA" \
  --planner qwen3:0.6b \
  --synthesizer phi4-mini             # Especifica modelos manualmente

# ── Modelos ──────────────────────────────────────────────────────────────────
bauer models list                     # Lista modelos do models.yaml com perfis
bauer models test MODELO              # Testa modelo: RAM, contexto, tool mode

# ── Ferramentas (Tool Bridge) ─────────────────────────────────────────────────
bauer tools list                      # Lista tools disponíveis (config-aware)
bauer tools run '{"action":"list_dir","args":{"path":"."}}' # Executa tool direto
bauer tools run action.json           # Aceita arquivo .json (útil no Windows)

# ── Projeto e tarefas ────────────────────────────────────────────────────────
bauer project init NOME               # Inicializa workspace com PROJECT.md + TASKS.md
bauer project status                  # Mostra PROJECT.md e resumo de tarefas

bauer task add "Titulo da tarefa"     # Adiciona tarefa ao TASKS.md
bauer task list                       # Lista todas as tarefas com status
bauer task list --status TODO         # Filtra por status
bauer task start ID                   # Marca tarefa como IN_PROGRESS
bauer task done ID                    # Marca tarefa como DONE
bauer task block ID                   # Marca tarefa como BLOCKED
bauer task board                      # Kanban board no terminal (4 colunas)
bauer task board --compact            # Board compacto (sem descrição)

# ── Memória ──────────────────────────────────────────────────────────────────
bauer memory summarize                # Força resumo da memória atual

# ── Aprendizado ──────────────────────────────────────────────────────────────
bauer learning show                   # Exibe experiências registradas
bauer learning explain                # Explica decisões automáticas
bauer learning reset                  # Apaga histórico de aprendizado
bauer learning forget-model MODELO    # Remove experiências de um modelo específico

# ── Servidor HTTP ────────────────────────────────────────────────────────────
bauer serve                           # Inicia servidor FastAPI em 0.0.0.0:8000
bauer serve --host 127.0.0.1 --port 9000
bauer serve --api-key minha-chave     # Protege todos os endpoints com Bearer token

# ── Logs ─────────────────────────────────────────────────────────────────────
bauer logs follow                     # Tail em tempo real do arquivo de log
```

---

## 30. Docker Compose

O Bauer Agent inclui um stack Docker Compose completo para execução sem configuração manual do Ollama.

### 30.1 Arquitetura dos containers

```txt
┌─────────────────────────────────────────────────────┐
│  Docker Compose Stack                               │
│                                                     │
│  ┌──────────────┐   rede interna   ┌─────────────┐  │
│  │ bauer-ollama │ ◄──────────────► │ bauer-agent │  │
│  │ (sem porta)  │                  │  :8000 ◄────┼──┼── usuário
│  └──────────────┘                  └─────────────┘  │
│         ▲                                           │
│  ┌──────┴────────┐                                  │
│  │ ollama-init   │  (baixa modelos, depois sai)     │
│  └───────────────┘                                  │
└─────────────────────────────────────────────────────┘
```

Princípios:
- `bauer-ollama` não tem porta exposta — só acessível via rede Docker interna
- `bauer-agent` é o único ponto externo (porta 8000)
- `ollama-init` baixa os modelos na primeira vez e sai com código 0
- `bauer-agent` só inicia após `ollama-init` concluir com sucesso

### 30.2 docker-compose.yml resumido

```yaml
services:
  ollama:
    image: ollama/ollama:latest
    container_name: bauer-ollama
    volumes:
      - ollama_models:/root/.ollama    # modelos persistem entre restarts
    environment:
      - OLLAMA_KEEP_ALIVE=0            # libera RAM imediatamente após cada resposta
    healthcheck:
      test: ["CMD", "ollama", "list"]  # não usa curl (não está na imagem)
      interval: 10s
      retries: 10
      start_period: 30s

  ollama-init:
    image: ollama/ollama:latest
    depends_on:
      ollama: {condition: service_healthy}
    environment:
      - OLLAMA_HOST=http://ollama:11434
    entrypoint: >
      sh -c "ollama pull qwen3:0.6b && ollama pull phi4-mini && ollama pull Impulse2000/smollm3"
    restart: "no"

  bauer:
    build: .
    container_name: bauer-agent
    ports:
      - "8000:8000"
    environment:
      - OLLAMA_HOST=http://ollama:11434
    depends_on:
      ollama: {condition: service_healthy}
      ollama-init: {condition: service_completed_successfully}
    restart: unless-stopped
```

### 30.3 Por que OLLAMA_KEEP_ALIVE=0

O Ollama por padrão mantém o modelo em RAM por 5 minutos após cada uso. Em máquinas com 8–12 GB RAM isso causa OOM se dois modelos forem carregados (ex: orquestrador + executor).

Com `OLLAMA_KEEP_ALIVE=0` o modelo é descarregado imediatamente após cada resposta. Impacto:
- Primeiro token mais lento (carrega da VRAM/disco)
- RAM nunca acumula dois modelos ao mesmo tempo
- Ideal para CPU/baixa RAM — essencial com `parallel_steps=False`

### 30.4 start.sh — modo Compose vs standalone

O `start.sh` detecta automaticamente o ambiente:

```bash
if [ -n "$OLLAMA_HOST" ]; then
    # Modo Docker Compose: Ollama já roda como serviço separado
    echo "[bauer] Usando Ollama externo: $OLLAMA_HOST"
    exec bauer serve --host 0.0.0.0 --port 8000
else
    # Modo standalone: sobe Ollama localmente
    ollama serve &
    # aguarda Ollama ficar disponível via python3 (curl não existe na imagem)
    until python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:11434/api/tags', timeout=2)" 2>/dev/null; do sleep 2; done
    ollama pull "${BAUER_MODEL:-qwen3:0.6b}"
    exec bauer serve --host 0.0.0.0 --port 8000
fi
```

### 30.5 Comandos de operação

```bash
# Subir tudo (baixa modelos na primeira vez, ~5 GB)
docker compose up -d

# Acompanhar download dos modelos
docker logs -f bauer-ollama-init

# Ver status dos containers
docker compose ps

# Ver logs do bauer-agent
docker logs bauer-agent --tail 50

# Reiniciar apenas o bauer-agent (sem re-baixar modelos)
docker compose restart bauer

# Parar tudo (modelos ficam no volume — não são re-baixados)
docker compose down

# Remover tudo incluindo volumes (re-baixa modelos no próximo up)
docker compose down -v
```

### 30.6 Volumes

```txt
ollama_models    — modelos Ollama (~5 GB com qwen3:0.6b + phi4-mini + smollm3)
bauer_workspace  — arquivos do workspace do agente
bauer_memory     — memória persistente (Markdown + sessões)
bauer_logs       — logs do servidor
```

### 30.7 GPU (NVIDIA)

Para usar GPU, descomente o bloco no `docker-compose.yml`:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

E altere o perfil no `config.yaml`:

```yaml
runtime:
  profile: high   # habilita parallel_steps no orquestrador
```

---

## 31. Kanban Board no terminal

O Bauer Agent inclui um Kanban board visualizado diretamente no terminal, sem depender de interface web.

### 31.1 Comando

```bash
bauer task board           # Board completo com descrições
bauer task board --compact # Board compacto (só ID e título)
```

### 31.2 Layout

```txt
╭──────────────────╮ ╭──────────────────╮ ╭──────────────────╮ ╭──────────────────╮
│ 📋 TODO (2)      │ │ 🔄 IN PROGRESS(1)│ │ ✅ DONE (3)      │ │ 🚫 BLOCKED (1)  │
├──────────────────┤ ├──────────────────┤ ├──────────────────┤ ├──────────────────┤
│ [001] Criar API  │ │ [003] Refatorar  │ │ [002] Testes     │ │ [005] Deploy     │
│       Desc...    │ │       schema...  │ │ [004] Docs       │ │       Aguarda... │
│ [006] Novo UI    │ │                  │ │ [007] Docker     │ │                  │
╰──────────────────╯ ╰──────────────────╯ ╰──────────────────╯ ╰──────────────────╯

  Progresso: ████████████░░░░░░░░ 60%  (3/5 concluídas)
```

### 31.3 Compatibilidade de terminal

O board detecta automaticamente o suporte a UTF-8:

```txt
Linux/Mac (UTF-8):    📋 ✅ 🚫 🔄 — barras █░
Windows UTF-8:        mesmos emojis
Windows legacy CP1252: [ ] [x] [!] [~] — barras #.
```

### 31.4 Ciclo de vida de uma tarefa

```bash
bauer task add "Implementar feature X"     # → TODO
bauer task start 001                        # → IN_PROGRESS
bauer task done 001                         # → DONE
# ou
bauer task block 001                        # → BLOCKED
```

---

## 32. Hermes Agent vs Bauer Agent — Comparativo completo

O Bauer Agent foi criado para resolver problemas concretos do Hermes Agent. Este comparativo mostra o que foi resolvido e o que o Bauer vai além.

### 32.1 Problemas do Hermes que o Bauer resolve

| Problema no Hermes | Comportamento Hermes | Solução no Bauer |
|---|---|---|
| **Contexto rígido (64K)** | Falha se o modelo não suportar 64K | `auto_downgrade_context: true` — usa o menor entre: pedido, modelo real, RAM segura |
| **Tools obrigatórias** | Modelo sem tool calling → crash | Tool Bridge — modelo responde JSON, Bauer executa fora |
| **Config ≠ Runtime** | Config pede modelo A, runtime usa modelo B sem avisar | `runtime_state.json` — fonte única da verdade; doctor detecta e corrige |
| **RAM sem controle** | Modelo 7B + 64K context → OOM silencioso | Preflight calcula RAM segura; auto-downgrade de modelo se necessário |
| **Sem feedback de falha** | Erro vago sem diagnóstico | `bauer doctor` com tabela detalhada: Ollama, modelo, RAM, contexto, tool mode |

### 32.2 Funcionalidades presentes em ambos

| Funcionalidade | Hermes | Bauer |
|---|---|---|
| Chat local com Ollama | ✅ | ✅ |
| Suporte a .env | ✅ | ✅ |
| OpenAI API | ✅ | ✅ |
| OpenRouter | ✅ | ✅ |
| Seletor interativo de modelo | ✅ (`hermes model`) | ✅ (`bauer model`) |
| Auth OAuth PKCE (OpenAI) | ✅ | ✅ |
| Auth API Key | ✅ | ✅ |

### 32.3 Funcionalidades exclusivas do Bauer

| Funcionalidade | Descrição |
|---|---|
| **Tool Bridge** | Tools sem depender de tool calling nativo no modelo |
| **Contexto adaptativo** | `auto_downgrade_context` — reduz contexto se a RAM não aguenta |
| **Auto-seleção de modelo** | Troca para modelo menor automaticamente se RAM insuficiente |
| **ModelRouter** | Classifica cada mensagem e envia para o melhor modelo (qwen3/smollm3/phi4-mini) |
| **AgentOrchestrator** | Decomposição em DAG, execução por ondas, paralelo ou sequencial, --resume |
| **Adaptive Learning Engine** | Aprende modelos estáveis, falhas, preferências — sem mudanças invisíveis |
| **Skill Registry** | Sugere skills com base em comandos repetidos |
| **Performance Tracker** | Rastreia tempo de resposta e tokens por sessão por modelo |
| **Self Tuner** | Auto-ajuste de context budget com base em histórico de falhas |
| **Kanban Board** | `bauer task board` — 4 colunas no terminal |
| **bauer doctor** | Diagnóstico completo antes de iniciar (RAM, modelo, contexto, tools) |
| **Docker Compose** | Stack completo: Ollama interno + Bauer exposto; init automático de modelos |
| **REST API (FastAPI)** | `/chat`, `/stream` (SSE), `/sessions`, `/tools`, `/models/switch` |
| **Web UI** | Interface HTML servida pelo `bauer serve` |
| **OLLAMA_KEEP_ALIVE=0** | Libera RAM após cada resposta — essencial para CPU/baixa RAM |

### 32.4 Filosofia central

```txt
Hermes:
  "Sem 64K + tools nativas → não rodo."

Bauer:
  "Rodo com o que tem, ajusto o que precisar, mostro tudo claramente."
  "Modelo pequeno → agente simples."
  "Modelo médio → agente com memória resumida."
  "Modelo forte → agente com contexto maior e tools nativas."
  "Falha anterior → ajuste melhor na próxima vez."
  "Decisão automática → sempre visível e reversível."
```

### 32.5 Quando usar Hermes vs Bauer

```txt
Use Hermes quando:
  ✓ Máquina tem 16 GB+ RAM
  ✓ Modelo suporta tool calling nativo
  ✓ Quer a experiência "premium" sem compromisso

Use Bauer quando:
  ✓ VPS com 4–8 GB RAM
  ✓ CPU only (sem GPU)
  ✓ Modelos locais leves (0.6B, 3B, mini)
  ✓ Quer agente que aprende e se adapta
  ✓ Precisa de orquestração multi-passo
  ✓ Quer REST API + Docker Compose prontos
```

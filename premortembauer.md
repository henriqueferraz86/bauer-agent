# Premortem — Bauer Agent

> Atualizado com riscos da camada de auto adaptação, aprendizado contínuo e LoRA/QLoRA.


## Cenário

Estamos 90 dias no futuro.

O Bauer Agent falhou.

Não porque a ideia era ruim, mas porque o projeto tentou resolver muita coisa ao mesmo tempo: agente, tools, memória, Ollama, contexto, CLI, segurança, perfis, runtime adaptativo e aprendizado contínuo.

O problema principal foi falta de corte no MVP.

---

# 1. Falha: virar um “Hermes 2” pesado

## Como acontece

O Bauer começa simples, mas vai copiando tudo que o Hermes faz:

```txt
Tools complexas
Memória vetorial
Múltiplos agentes
RAG
Workspace
Config avançada
Web UI
Autonomia
Scheduler
Integrações
```

Resultado: fica tão pesado quanto o Hermes.

## Impacto

O projeto perde o diferencial.

O Bauer nasceu para ser leve, mas vira mais uma stack difícil de subir.

## Prevenção

MVP precisa ser seco:

```txt
Doctor
Chat
Config validate
Ollama check
Context auto-adjust
Tool bridge simples
```

Nada de RAG no começo.

Nada de multi-agent no começo.

Nada de web UI no começo.

---

# 2. Falha: contexto adaptativo virar gambiarra

## Como acontece

A config pede 64K.

O Ollama sobe com 32K.

O modelo aceita outro valor.

A RAM aguenta menos.

O Bauer tenta ajustar automaticamente, mas não deixa claro o que está acontecendo.

## Impacto

O usuário continua confuso:

```txt
Configurei 64K, mas está rodando quanto?
O modelo ativo é esse mesmo?
O Ollama respeitou a config?
```

## Prevenção

Criar sempre um arquivo real de estado:

```txt
.runtime_state.json
```

Com:

```json
{
  "configured_model": "qwen2.5-coder:7b",
  "active_model": "qwen2.5-coder:7b",
  "requested_context": 64000,
  "applied_context": 32768,
  "reason": "RAM limit",
  "tool_mode": "bridge"
}
```

Regra: **sem estado visível, não inicia.**

---

# 3. Falha: confiar demais no Ollama

## Como acontece

O Bauer pergunta para o Ollama qual modelo está disponível, mas assume que o contexto informado é real.

Só que o Ollama pode:

```txt
Carregar modelo com contexto menor
Ignorar variável
Usar Modelfile diferente
Manter processo antigo ativo
Subir com env antigo
```

## Impacto

Mesmo com config certa, o runtime fica errado.

## Prevenção

O `bauer doctor` precisa testar de verdade:

```txt
1. Ollama está ativo?
2. Modelo existe?
3. Modelo responde?
4. Contexto aplicado parece compatível?
5. Processo do Ollama foi iniciado com qual env?
6. Existe OLLAMA_CONTEXT_LENGTH ativo?
```

E mostrar comando de correção.

---

# 4. Falha: tool bridge inseguro

## Como acontece

Modelo sem tool calling nativo responde algo como:

```json
{
  "action": "run_command",
  "args": {
    "command": "rm -rf workspace"
  }
}
```

O Bauer executa.

## Impacto

Risco real de apagar arquivo, vazar token ou quebrar servidor.

## Prevenção

Tool bridge precisa ter três camadas:

```txt
1. Allowlist de tools
2. Sandbox de diretório
3. Confirmação para comando perigoso
```

No MVP, `run_command` deve ser limitado.

Melhor começar só com:

```txt
list_dir
read_file
write_file dentro do workspace
search_text
```

Deixar shell para depois.

---

# 5. Falha: tentar suportar todos os modelos

## Como acontece

O projeto tenta funcionar com:

```txt
Qwen
Llama
DeepSeek
Mistral
Phi
Gemma
CodeLlama
OpenAI
Anthropic
OpenRouter
```

Cada um tem contexto, formato, tools e comportamento diferente.

## Impacto

O Bauer vira manutenção infinita.

## Prevenção

No MVP, suportar só:

```txt
Ollama + modelo sem tools
OpenAI-compatible API depois
```

E começar com poucos modelos testados:

```txt
qwen2.5-coder:3b
qwen2.5-coder:7b
llama3.1:8b
```

---

# 6. Falha: memória automática ficar ruim

## Como acontece

O Bauer resume conversas longas, mas perde decisões importantes.

Exemplo:

```txt
Usuário decidiu usar qwen2.5-coder:3b
Resumo esquece isso
Agente volta a sugerir 7B
```

## Impacto

O agente parece burro.

Repete decisões antigas.

## Prevenção

Separar memória em arquivos fixos:

```txt
MEMORY.md
DECISIONS.md
TASKS.md
RUNTIME.md
```

Não confiar só em resumo automático.

Toda decisão técnica deve ir para `DECISIONS.md`.

---

# 7. Falha: “auto mode” tomar decisões ruins

## Como acontece

O Bauer detecta pouca RAM e escolhe modelo muito fraco.

Ou detecta modelo forte e sobe contexto alto demais.

## Impacto

Ou fica lento, ou quebra.

## Prevenção

O auto mode deve ser conservador.

Regra:

```txt
Na dúvida, reduz contexto.
Na dúvida, desliga tools perigosas.
Na dúvida, usa profile low.
```

E sempre mostrar:

```txt
Modo escolhido: low
Motivo: RAM disponível abaixo de X MB
```

---

# 8. Falha: CLI bonita, mas agente fraco

## Como acontece

Muito tempo gasto em:

```txt
Rich
Tabela bonita
Logs coloridos
Comandos elegantes
```

Mas o chat, tools e contexto ficam instáveis.

## Impacto

Parece bom, mas não resolve o problema.

## Prevenção

Prioridade real:

```txt
1. Doctor confiável
2. Runtime simples
3. Contexto correto
4. Tool bridge seguro
5. Logs claros
6. Beleza depois
```

---

# 9. Falha: não ter logs úteis

## Como acontece

Quando dá erro, aparece só:

```txt
Model failed
Context error
Tool error
```

## Impacto

O Bauer repete o problema do Hermes: erro confuso.

## Prevenção

Todo erro precisa ter:

```txt
Causa provável
Valor configurado
Valor detectado
Ação sugerida
```

Exemplo:

```txt
Erro: contexto solicitado acima do seguro.

Configurado: 64000
Detectado no Ollama: 32768
Seguro para RAM: 16384
Aplicado: 16384

Correção:
- reduzir requested_context para 16384
- ou usar modelo menor
- ou aumentar RAM
```

---

# 10. Falha: usar modelo 7B como padrão

## Como acontece

O projeto escolhe `qwen2.5-coder:7b` como padrão.

Na VPS fraca, ele pesa demais.

## Impacto

O Bauer já nasce lento ou quebrando.

## Prevenção

Padrão do Bauer deve ser leve:

```txt
qwen2.5-coder:3b
contexto 8192 ou 16384
tool_bridge
profile low
```

O 7B entra como perfil médio.

---

# 11. Falha: misturar config desejada com config aplicada

## Como acontece

`config.yaml` tem uma coisa.

O runtime usa outra.

O usuário não sabe qual venceu.

## Impacto

Mesmo bug do Hermes.

## Prevenção

Separar três camadas:

```txt
config.yaml          -> o que o usuário quer
models.yaml          -> limites conhecidos
.runtime_state.json  -> o que foi aplicado
```

Nunca sobrescrever silenciosamente.

---

# 12. Falha: autonomia cedo demais

## Como acontece

Logo no começo, o Bauer tenta:

```txt
Executar tarefas sozinho
Editar arquivos
Rodar comandos
Instalar dependências
Corrigir projeto
```

## Impacto

Risco alto e bugs difíceis.

## Prevenção

Fases:

```txt
v0.1 = diagnóstico
v0.2 = chat
v0.3 = leitura de arquivos
v0.4 = escrita controlada
v0.5 = comandos seguros
v0.6 = agente de projeto
```

Nada de autonomia total antes da v0.6.

---


# 13. Falha: aprendizado automático virar bagunça

## Como acontece

O Bauer começa a aprender com erros e desempenho, mas registra conclusões ruins.

Exemplos:

```txt
Um erro temporário de RAM faz o Bauer banir um modelo bom.
Uma falha de rede vira conclusão falsa de que o modelo é ruim.
Um resumo ruim vira memória permanente.
Uma preferência antiga do usuário continua sendo aplicada mesmo depois de mudar o cenário.
Uma skill sugerida vira automação perigosa cedo demais.
```

## Impacto

O agente passa a tomar decisões erradas automaticamente.

O usuário perde confiança porque não entende por que o Bauer mudou modelo, contexto ou perfil.

O Bauer deixa de ser adaptativo e vira imprevisível.

## Prevenção

O aprendizado precisa ser:

```txt
Auditável
Reversível
Conservador
Explicável
Limitado por confiança
```

Regras obrigatórias:

```txt
Não aplicar aprendizado sem registrar motivo.
Não criar tool executável automaticamente.
Não transformar uma falha isolada em regra permanente.
Não alterar config.yaml silenciosamente.
Manter histórico separado em MODEL_EXPERIENCE.md e FAILED_ATTEMPTS.md.
Permitir reset e forget por modelo.
```

Comandos obrigatórios:

```bash
bauer learning show
bauer learning explain
bauer learning reset
bauer learning forget-model qwen2.5-coder:7b
```

Exemplo de saída correta:

```txt
Ajuste aplicado por aprendizado:
Modelo alterado de qwen2.5-coder:7b para qwen2.5-coder:3b.
Motivo: 3 falhas anteriores de RAM com 7B nesta VPS.
Confiança: alta.
Para desfazer: bauer learning forget-model qwen2.5-coder:7b
```

---


# 14. Falha: usar LoRA/QLoRA cedo demais

## Como acontece

O projeto tenta treinar adaptadores antes de ter o runtime estável.

A equipe começa a gastar tempo com:

```txt
Dataset de fine-tuning
Receitas LoRA
Receitas QLoRA
Avaliação de adapter
Checkpoints
Quantização
Treino em GPU
```

Mas o básico ainda não está sólido:

```txt
Doctor
Config validate
Runtime state
Contexto adaptativo
Tool Bridge seguro
Logs claros
```

## Impacto

O Bauer ganha complexidade antes da hora.

LoRA/QLoRA pode virar uma muleta para esconder falha de arquitetura.

Exemplo ruim:

```txt
O doctor não detecta contexto errado.
Em vez de corrigir o doctor, tenta treinar o modelo para responder melhor sobre contexto.
```

Isso não resolve o problema real.

## Prevenção

Regra dura:

```txt
Primeiro o Bauer precisa funcionar bem sem fine-tuning.
Depois LoRA/QLoRA entra como refinamento opcional.
```

LoRA/QLoRA só deve entrar quando existir:

```txt
Runtime estável.
Logs confiáveis.
Erros reais coletados.
Dataset pequeno validado.
Métrica de avaliação.
Rollback fácil do adapter.
```

Regras obrigatórias:

```txt
Adapters são opcionais.
Adapters não podem ser dependência do MVP.
Adapters não substituem doctor, runtime_state ou tool_bridge.
Adapters não devem alterar comportamento crítico sem avaliação.
Adapters precisam poder ser desligados.
```

Comandos úteis:

```bash
bauer adapter list
bauer adapter eval bauer-diagnostico
bauer adapter enable bauer-diagnostico
bauer adapter disable bauer-diagnostico
```

---

# Top 7 riscos reais

| Risco | Gravidade | Chance | Ação |
|---|---:|---:|---|
| Escopo grande demais | Alta | Alta | Cortar MVP |
| Tool bridge inseguro | Alta | Média | Sandbox + allowlist |
| Config divergente | Alta | Alta | `.runtime_state.json` |
| RAM insuficiente | Alta | Alta | Profile low como padrão |
| Memória ruim | Média | Alta | `DECISIONS.md` separado |
| Aprendizado automático errado | Alta | Média | Learning auditável e reversível |
| LoRA/QLoRA cedo demais | Média | Média | Deixar como fase futura opcional |

---

# Decisão dura

O Bauer Agent não deve começar como agente completo.

Deve começar como:

```txt
Um runtime confiável para rodar LLM local sem erro burro de contexto, RAM e tools.
```

Essa é a proposta forte.

Não é “mais um agente”.

É um **anti-Hermes-problem agent**.

---

# MVP correto

## Bauer Agent v0.1

Entregar só isso:

```txt
bauer doctor
bauer config validate
bauer models list
bauer chat
contexto adaptativo
runtime_state
profile low/medium
learning observe básico
```

Sem:

```txt
RAG
Multi-agent
UI web
Scheduler
Autonomia total
Integrações externas
Aprendizado agressivo
Criação automática de tools executáveis
LoRA/QLoRA obrigatório
Fine-tuning no MVP
```

---

# Versão mais segura do plano

```txt
Fase 1:
Resolver diagnóstico e config.

Fase 2:
Rodar chat local com contexto adaptativo.

Fase 3:
Adicionar memória simples por markdown.

Fase 4:
Adicionar tool bridge sem shell.

Fase 5:
Adicionar shell controlado.

Fase 6:
Adicionar agente de projeto.

Fase 7:
Adicionar Adaptive Learning Engine em modo conservador.

Fase 8:
Adicionar LoRA/QLoRA Adapter apenas como refinamento opcional.
```

---

# Veredito

O plano é bom.

Mas o risco é querer construir o agente completo cedo demais.

O Bauer vence se fizer uma coisa muito bem primeiro:

```txt
Subir modelo local pequeno/médio sem quebrar por contexto, tools ou RAM.
```

Depois disso, o Bauer pode aprender com o uso, mas só de forma visível, reversível e conservadora.

Esse deve ser o núcleo do projeto.

LoRA/QLoRA pode ajudar depois, mas não pode virar fundação do Bauer.

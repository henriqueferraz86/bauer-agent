# Bauer Agent — Decisões duras em aberto

> Documento de decisões técnicas que precisam ser fechadas **antes** da Fase 1.
> Cada decisão tem: contexto, proposta concreta, motivo, alternativa rejeitada.

Status: **aprovado em 2026-05-27** — todas as 5 decisões seguem como propostas.
Fase 1 inicia com essas decisões em vigor.

---

## Decisão 1 — Modelo padrão do `config.yaml`

### Contexto

`BauerAgent.md` (seção 10) usa `qwen2.5-coder:7b` como padrão no `config.yaml` de exemplo.
`premortembauer.md` (item 10) marca isso como falha: "o Bauer já nasce lento ou quebrando" em VPS fraca.

Os documentos discordam entre si. Quem copiar o exemplo cai direto na falha que o premortem previu.

### Proposta

Padrão do `config.yaml`:

```yaml
model:
  provider: ollama
  name: qwen2.5-coder:3b
  requested_context: 16384
  minimum_context: 8192
  auto_downgrade_context: true

runtime:
  ram_limit_mb: 4096
  profile: low
```

O `qwen2.5-coder:7b` passa a ser o padrão **somente** quando o usuário rodar `bauer run --profile medium` ou marcar explicitamente no `config.yaml`.

### Motivo

A premissa do Bauer é "subir sem dor". Padrão deve ser conservador. Quem tem máquina forte sobe um nível por escolha; quem tem VPS fraca não precisa fazer nada para evitar a primeira pancada.

### Alternativa rejeitada

Manter 7B como padrão com aviso. Rejeitada porque o aviso não impede a falha — o usuário só descobre o problema depois de o processo já ter consumido RAM.

---

## Decisão 2 — Como detectar contexto aplicado de verdade

### Contexto

O `BauerAgent.md` (seção 5.1, 6) diz que o doctor precisa mostrar o "contexto aplicado".
O `premortembauer.md` (item 3) reconhece que o Ollama mente: pode carregar com contexto menor, ignorar variável, manter processo antigo, subir com env diferente.

Hoje não está definido **como** o Bauer mede isso.

### Proposta

Duas camadas:

**Camada A — barata, sempre roda no `doctor`.**

1. Ler `OLLAMA_CONTEXT_LENGTH` do ambiente do processo Ollama (via `/proc/<pid>/environ` no Linux).
2. Consultar `GET /api/show` do Ollama com o nome do modelo e ler `parameters.num_ctx` do Modelfile.
3. Comparar os dois. Se divergirem, mostrar os dois valores e o motivo provável.

**Camada B — opcional, roda em `bauer doctor --deep`.**

Sonda empírica: enviar um prompt construído com tokens numerados sequenciais (`token_0 token_1 ... token_N`) somando ~110% do contexto esperado e pedir ao modelo que repita o primeiro token. O ponto em que o modelo perde o início é o limite real.

Resultado escrito em `.runtime_state.json`:

```json
{
  "context": {
    "requested": 64000,
    "modelfile_num_ctx": 32768,
    "env_OLLAMA_CONTEXT_LENGTH": null,
    "applied": 32768,
    "empirical_probe": null,
    "reason": "modelfile_default_overrode_request"
  }
}
```

### Motivo

A sonda empírica é cara (custa um round-trip longo) então fica opt-in. As duas leituras baratas pegam 90% dos casos sem custo. Mostrar os dois valores impede a sensação de "qual venceu" que o premortem identificou como o pior bug do Hermes.

### Alternativa rejeitada

Confiar apenas no `/api/show`. Rejeitada porque o premortem item 3 já cataloga os jeitos do Ollama desrespeitar o Modelfile.

---

## Decisão 3 — Fórmula de RAM segura

### Contexto

`BauerAgent.md` seções 4 e 6 dizem "contexto seguro baseado na RAM" mas não definem a fórmula. Sem fórmula, cada implementação chuta.

### Proposta

Tabelar consumo base por modelo no `models.yaml` e calcular o contexto seguro a partir dele:

```yaml
models:
  qwen2.5-coder:3b:
    ram_base_mb: 2400
    ram_per_1k_ctx_mb: 35
    max_context_safe: 32768

  qwen2.5-coder:7b:
    ram_base_mb: 5200
    ram_per_1k_ctx_mb: 70
    max_context_safe: 32768

  llama3.1:8b:
    ram_base_mb: 5800
    ram_per_1k_ctx_mb: 80
    max_context_safe: 32768
```

Fórmula no `context_manager.py`:

```python
def contexto_seguro(modelo, ram_disponivel_mb, folga_mb=1024):
    ram_para_contexto = ram_disponivel_mb - modelo.ram_base_mb - folga_mb
    if ram_para_contexto <= 0:
        return 0  # modelo não cabe nem vazio nesta máquina
    tokens_seguros = (ram_para_contexto / modelo.ram_per_1k_ctx_mb) * 1024
    return min(int(tokens_seguros), modelo.max_context_safe)
```

`ram_disponivel_mb` vem de `psutil.virtual_memory().available / 1024 / 1024`, não do total.

Valores iniciais de `ram_base_mb` e `ram_per_1k_ctx_mb` saem de medição manual em uma máquina de referência. O `learning_engine` (Fase 7) ajusta esses números com dados reais por máquina ao longo do tempo.

### Motivo

Coloca a tabela no `models.yaml` (auditável, editável) em vez de hardcoded em Python. Permite que o usuário corrija manualmente se sua medição local diferir. Folga de 1024 MB protege o sistema operacional.

### Alternativa rejeitada

Calcular dinamicamente carregando o modelo e medindo. Rejeitada porque o doctor precisa ser rápido e idempotente — não pode subir e descer modelo a cada execução.

---

## Decisão 4 — Markdown vs SQLite para persistência

### Contexto

A stack recomendada em `BauerAgent.md` seção 17 lista SQLite. A memória descrita na seção 7 e em todos os exemplos é Markdown. Os dois nunca se encontram no documento — não fica claro o que vai onde.

### Proposta

Regra simples:

**Markdown — para coisas que o humano lê.**
- `MEMORY.md`, `DECISIONS.md`, `TASKS.md`
- `MODEL_EXPERIENCE.md`, `FAILED_ATTEMPTS.md`, `USER_PREFERENCES.md`
- `RUNTIME_LESSONS.md`, `SKILLS_LEARNED.md`

**SQLite — para coisas que o agente consulta com query.**
- `metrics.db`: latência por modelo, tokens/s, RAM medida, timestamps. Tabela `runs(model, context, latency_ms, ram_peak_mb, success, ts)`.
- Nada mais. Sem ORM, sem migrations elaboradas — `sqlite3` da stdlib + uma função `record_run()`.

**Não Markdown e não SQLite:** `.runtime_state.json` continua JSON puro porque é estado efêmero de uma execução.

### Motivo

Markdown ganha pelos arquivos que o usuário precisa abrir no editor para auditar ou corrigir. SQLite ganha onde o agente precisa responder "qual foi a latência média do qwen:7b com 16K nos últimos 30 dias" — fazer isso em Markdown vira parser de regex.

### Alternativa rejeitada

Tudo em Markdown. Rejeitada porque telemetria de performance precisa de agregação; varrer 10000 linhas de Markdown a cada decisão do learning_engine é lento e frágil.

Tudo em SQLite. Rejeitada porque tira a auditoria humana — o usuário não vai abrir um SQL client para corrigir uma preferência.

---

## Decisão 5 — Fingerprint da máquina para tornar o aprendizado portável

### Contexto

O premortem identifica que aprendizado errado é risco alto (item 13), mas não cobre o caso de **mudar de máquina**. Se o usuário levar `MODEL_EXPERIENCE.md` da VPS antiga para uma nova com mais RAM, as lições viram falsas — o Bauer vai recomendar contexto baixo "porque travou antes" mesmo que agora caiba folgado.

### Proposta

Cada lição aprendida carrega um `machine_id` curto e determinístico:

```python
import hashlib, platform, psutil

def machine_id():
    parts = [
        platform.node(),                               # hostname
        platform.machine(),                            # arch
        str(round(psutil.virtual_memory().total / 1e9))  # RAM em GB arredondado
    ]
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]
```

Exemplo no `MODEL_EXPERIENCE.md`:

```markdown
## qwen2.5-coder:7b — falha de RAM
- machine_id: a3f9c1b2d4e5
- requested_context: 64000
- applied_context: 32768
- result: oom_kill
- ts: 2026-05-27T14:30:00
- recommendation: usar 3b com 16K nesta máquina
```

No `learning_engine`, ao consultar lições passadas, filtrar por `machine_id` atual. Lições de outras máquinas viram **referência fraca** (mostra como contexto, não aplica automaticamente).

Comando novo: `bauer learning import --from old_machine_id` para migrar lições explicitamente quando faz sentido.

### Motivo

Mantém o aprendizado útil quando o ambiente é o mesmo, mas evita falsos positivos quando muda. O hash curto (12 chars) é legível para humanos sem expor hostname completo nos arquivos sincronizados. Arredondar a RAM em GB evita que um upgrade de 16→32GB invalide tudo, mas o `total` no fingerprint garante que pular de 4 para 32 GB conta como máquina nova.

### Alternativa rejeitada

Não ter fingerprint e confiar em todas as lições. Rejeitada — o premortem item 13 já alerta para "preferência antiga continuar sendo aplicada mesmo depois de mudar o cenário".

Fingerprint baseado em UUID persistente. Rejeitada porque não sobrevive a reinstalações, e o usuário pode legitimamente reinstalar o sistema sem querer perder o histórico.

---

## Resumo das mudanças necessárias nos documentos atuais

| Documento | Seção | Mudança |
|---|---|---|
| `BauerAgent.md` | 10 (config.yaml) | Trocar modelo padrão para `qwen2.5-coder:3b`, contexto `16384` |
| `BauerAgent.md` | 5.1 + 6 | Adicionar referência a Decisão 2 e 3 |
| `BauerAgent.md` | 9 (models.yaml) | Adicionar campos `ram_base_mb` e `ram_per_1k_ctx_mb` |
| `BauerAgent.md` | 17 (stack) | Esclarecer Markdown vs SQLite conforme Decisão 4 |
| `premortembauer.md` | novo item 15 | Adicionar risco "trocar de máquina invalida aprendizado" |
| `Guia_Todas_Fases…` | Fase 1 | Incluir `machine_id` no `runtime_state.json` |

---

## Critério para fechar este documento

Cada decisão acima precisa de uma de três respostas do dono do projeto:

1. **Aprovado como está.**
2. **Aprovado com ajuste:** [qual].
3. **Rejeitado, alternativa:** [qual].

Sem aprovação dessas cinco, a Fase 1 começa com ambiguidade — e ambiguidade na fundação é o que o premortem inteiro está tentando evitar.

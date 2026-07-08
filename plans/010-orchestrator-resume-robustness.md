# Plan 010: Robustez do orquestrador — validar `StepResult` no `--resume` e avisar em DAG circular

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. If
> anything in "STOP conditions" occurs, stop and report. When done, update this
> plan's status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 2c9d86f..HEAD -- bauer/orchestrator.py`
> If `bauer/orchestrator.py` changed, compare "Current state" excerpts against
> live code before proceeding; on mismatch, treat as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: correctness
- **Planned at**: commit `2c9d86f`, 2026-07-06

## Why this matters

Duas fragilidades no orquestrador multi-passo (`bauer orchestrate run --resume`):

1. **`load_progress` desserializa estado do disco sem validação**:
   `StepResult(**d)` desempacota um dict arbitrário de um arquivo JSON. Se um
   `step_*.json` estiver corrompido, truncado, ou vier de uma versão antiga
   com schema diferente, isso lança `TypeError` cru (campo faltando/extra) e
   **aborta o `--resume` inteiro** — justamente o mecanismo que deveria
   recuperar uma execução longa interrompida.

2. **Fallback de DAG circular é silencioso**: quando `_topological_batches`
   não encontra passos prontos (dependência circular ou plano inválido), ele
   processa tudo em sequência sem avisar. O usuário não sabe que o DAG que
   escreveu estava quebrado — o resultado sai "funcionando" mas a estrutura de
   paralelismo pretendida foi ignorada em silêncio.

Ambos são fixes pequenos que melhoram a confiabilidade e a depurabilidade do
pilar de autonomia, sem alterar o caminho feliz.

## Current state

- `bauer/orchestrator.py` — orquestrador (planeja → executa DAG/paralelo →
  sintetiza; persiste progresso para `--resume`).

Dataclass do resultado:

```python
# bauer/orchestrator.py:145-152
@dataclass
class StepResult:
    id: int
    goal: str
    model_used: str
    response: str
    tool_log: list[dict] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
```

Carregamento sem validação:

```python
# bauer/orchestrator.py:635-644
    def load_progress(self, task: str) -> list[StepResult]:
        """Carrega StepResults salvos. Retorna lista vazia se nao existir."""
        p = self._progress_path(task)
        if not p.exists():
            return []
        results = []
        for f in sorted(p.glob("step_*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            results.append(StepResult(**d))
        return results
```

Fallback silencioso de DAG:

```python
# bauer/orchestrator.py:324-338
        while remaining:
            ready = [
                s for s in remaining
                if all(d in completed for d in dag[s["id"]])
            ]
            if not ready:
                # Dependencia circular ou plano invalido — processa tudo restante em sequencia
                batches.extend([[s] for s in remaining])
                break
            batches.append(ready)
            for s in ready:
                completed.add(s["id"])
            remaining = [s for s in remaining if s["id"] not in completed]

        return batches
```

- O logger do módulo: verifique como o orchestrator loga (`grep -n "logger\|logging\|console" bauer/orchestrator.py | head`).
  Use o mesmo mecanismo já presente (provavelmente um `logging.getLogger` de
  módulo ou um `console` Rich passado). NÃO introduza um novo sistema de log.

### Convenções do repo a seguir
- Português em comentários/mensagens.
- Logging: reuse o logger/console já usado no arquivo. Se for `logging`, use
  `logger.warning(...)`; se o método tiver acesso a um `console` Rich, um
  `console.print("[yellow]...[/yellow]")` também serve — siga o padrão local.

## Commands you will need

| Purpose   | Command                                                          | Expected |
|-----------|------------------------------------------------------------------|----------|
| Testes    | `.venv/Scripts/python.exe -m pytest tests/ -k "orchestrat" -q`   | all pass |
| Import    | `.venv/Scripts/python.exe -c "import bauer.orchestrator"`        | exit 0   |

## Scope

**In scope**:
- `bauer/orchestrator.py` (`load_progress` + `_topological_batches`; opcional:
  um classmethod `StepResult.from_dict`)
- `tests/test_orchestrator_resume.py` (criar) — ou estender
  `tests/test_orchestrator.py` se preferir (veja qual existe).

**Out of scope** (NÃO tocar):
- A lógica de execução de passos (`execute_step`, `execute_parallel_steps`,
  retry) — só load/desserialização e detecção de ciclo.
- O formato de gravação (`save_progress`) — não mude o schema salvo; só torne a
  LEITURA tolerante.
- `execution_engine.py` — refactor de acoplamento orchestrator/engine é um
  achado separado, fora deste plano.

## Git workflow

- Branch: `advisor/010-orchestrator-resume-robustness`
- Commit style: conventional commits. Ex.:
  `fix(orchestrator): valida StepResult no --resume e avisa em DAG circular`
- NÃO faça push nem PR sem instrução.

## Steps

### Step 1: Desserialização tolerante em `load_progress`

Adicione um classmethod `StepResult.from_dict(d)` que:
- aceita só os campos conhecidos (`id, goal, model_used, response, tool_log,
  timestamp`), ignorando chaves extras (compat com versões futuras);
- valida tipos mínimos (`id` int, strings str); em caso de dict inválido,
  lança um `ValueError` claro OU retorna `None` para o caller pular o arquivo.

Depois, em `load_progress`, envolva a leitura de cada arquivo em try/except:
arquivo corrompido é **logado e pulado**, não aborta o resume inteiro.

Forma-alvo:

```python
@dataclass
class StepResult:
    id: int
    goal: str
    model_used: str
    response: str
    tool_log: list[dict] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, d: dict) -> "StepResult":
        """Constrói a partir de um dict de disco, tolerando chaves extras e
        validando os campos essenciais. Levanta ValueError se inválido."""
        try:
            return cls(
                id=int(d["id"]),
                goal=str(d.get("goal", "")),
                model_used=str(d.get("model_used", "")),
                response=str(d.get("response", "")),
                tool_log=list(d.get("tool_log", []) or []),
                timestamp=float(d.get("timestamp", 0.0) or 0.0),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"StepResult inválido: {exc}") from exc
```

```python
    def load_progress(self, task: str) -> list[StepResult]:
        """Carrega StepResults salvos. Retorna lista vazia se nao existir.
        Arquivos corrompidos/incompatíveis são pulados com aviso, sem abortar."""
        p = self._progress_path(task)
        if not p.exists():
            return []
        results = []
        for f in sorted(p.glob("step_*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                results.append(StepResult.from_dict(d))
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("Ignorando progresso corrompido %s: %s", f.name, exc)
                continue
        return results
```

(Se o módulo não tiver `logger`, use o mecanismo de log/console já presente —
ver "Convenções". Se nada existir, adicione `logger = logging.getLogger(__name__)`
no topo, seguindo o padrão de outros módulos como `bauer/gateway_runtime.py`.)

**Verify**: `.venv/Scripts/python.exe -c "import bauer.orchestrator"` → exit 0.

### Step 2: Aviso em DAG circular

Em `_topological_batches`, no ramo `if not ready:`, emita um aviso antes do
fallback sequencial, nomeando os passos que não puderam ser ordenados:

```python
            if not ready:
                stuck = [s["id"] for s in remaining]
                logger.warning(
                    "DAG com dependência circular ou inválida nos passos %s — "
                    "executando o restante em sequência.", stuck,
                )
                batches.extend([[s] for s in remaining])
                break
```

**Verify**: `.venv/Scripts/python.exe -c "import bauer.orchestrator"` → exit 0.

### Step 3: Testes

Crie/estenda os testes (ver Test plan).

**Verify**: `.venv/Scripts/python.exe -m pytest tests/ -k "orchestrat" -q` → all pass.

## Test plan

- Arquivo `tests/test_orchestrator_resume.py` (ou estenda `tests/test_orchestrator.py`).
  Veja qual existe: `ls tests/ | grep orchestrat`.
- Casos:
  1. **from_dict tolera chave extra**: `StepResult.from_dict({"id": 1, "goal": "g",
     "model_used": "m", "response": "r", "tool_log": [], "timestamp": 0.0,
     "campo_novo": "x"})` retorna um `StepResult` válido (ignora `campo_novo`).
  2. **from_dict rejeita dict inválido**: `StepResult.from_dict({"goal": "g"})`
     (sem `id`) levanta `ValueError`.
  3. **load_progress pula arquivo corrompido**: crie um dir de progresso em
     `tmp_path` com um `step_001.json` válido e um `step_002.json` com JSON
     inválido (`"{corrompido"`); `load_progress` retorna 1 resultado (o válido)
     e não levanta. (Descubra como o orchestrator resolve `_progress_path` —
     `grep -n "_progress_path" bauer/orchestrator.py` — e monte o fixture
     conforme.)
  4. **DAG circular não trava e avisa**: chame `_topological_batches` com dois
     passos que dependem um do outro (ciclo). Verifique que retorna batches
     (não trava) cobrindo ambos os passos. Para checar o aviso, use
     `caplog` do pytest (`caplog.set_level(logging.WARNING)`) e asserte que a
     mensagem de "circular" aparece.
- Verificação: `.venv/Scripts/python.exe -m pytest tests/test_orchestrator_resume.py -q`
  → all pass (≥4 casos).

## Done criteria

TODAS devem valer:

- [ ] `.venv/Scripts/python.exe -c "import bauer.orchestrator"` sai 0
- [ ] `.venv/Scripts/python.exe -m pytest tests/ -k "orchestrat" -q` passa (incl. novos)
- [ ] `grep -n "from_dict" bauer/orchestrator.py` retorna ≥2 (def + uso)
- [ ] `grep -n "StepResult(\*\*d)" bauer/orchestrator.py` NÃO retorna nada (substituído)
- [ ] `grep -n "dependência circular\|circular" bauer/orchestrator.py` retorna o aviso novo
- [ ] Nenhum arquivo fora do in-scope modificado (`git status`)
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

Pare e reporte se:

- Os excerpts de `StepResult`, `load_progress` ou `_topological_batches` não
  baterem com o código atual (drift).
- `_progress_path` gravar/ler em formato diferente do assumido (ex.: um único
  arquivo em vez de `step_*.json`) — reporte e não force o fixture.
- Não houver logger nem console acessível e adicionar `logging.getLogger` criar
  algum efeito colateral inesperado (improvável) — reporte.
- Um teste existente de orchestrator quebrar porque dependia do `TypeError` cru
  do `StepResult(**d)` — reporte (comportamento intencionalmente mudado).

## Maintenance notes

- Se `StepResult` ganhar campos novos no futuro, atualize `from_dict` para
  incluí-los; a tolerância a chaves extras garante que progresso antigo ainda
  carrega, mas campos novos precisam de default sensato.
- O reviewer deve conferir que `from_dict` não mascara bugs reais de gravação —
  ele tolera LEITURA de estado antigo, mas `save_progress` deve continuar
  gravando o schema completo.
- Follow-up separado (não incluído): refactor do acoplamento
  `orchestrator`/`execution_engine` (achado de tech-debt L, maior).

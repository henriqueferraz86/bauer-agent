# Plan 024: App Factory funciona pelo `bauer serve` / Desktop (tools expostas + contexto no system prompt)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat ced7dc2..HEAD -- bauer/commands/_runtime.py bauer/commands/serve_cmd.py bauer/app_factory.py bauer/agent.py`
> On any mismatch vs the "Current state" excerpts, treat as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/023 (recomendado — o factory depende das tools executarem de fato)
- **Category**: dx / direction
- **Planned at**: commit `ced7dc2`, 2026-07-18

## Why this matters

O App Factory (Spec-Driven Development com gates) só funciona pelo `bauer agent` (CLI). O `bauer serve` — que alimenta o Desktop, onde o usuário realmente trabalha — **não expõe as tools do factory nem injeta o estado dele no system prompt**. Consequência real: no Desktop o modelo não sabe que o App Factory existe, não conduz o fluxo de docs→gates, e **alucina** os docs (`docs/plan.md`, `features.md`) sem o factory estar iniciado. O objetivo do usuário é usar o App Factory como hábito **pelo Desktop**. Este plano fecha essa lacuna em duas frentes: (a) as tools `app_factory_init`/`app_factory_status` param de ser cortadas pelo allowlist automático; (b) o system prompt do serve passa a informar o projeto governado ativo, o gate atual e os docs pendentes — para o modelo conduzir o Spec-Driven Development.

## Current state

**(a) As tools do factory existem mas são cortadas.** Em `bauer/commands/_runtime.py`, o toolset enxuto aplicado a modelos locais (`_LOCAL_DEFAULT_ALLOWLIST`, linha 624) **não inclui** as tools do factory:

```python
_LOCAL_DEFAULT_ALLOWLIST = [
    "read_file", "write_file", "list_dir", "run_command",
    "web_search", "web_fetch", "search_text", "glob_files",
    "datetime_now", "calculate", "memory", "todo",
]
```

As tools `app_factory_init` e `app_factory_status` estão registradas no router (`bauer/tool_router.py:844,867`) e implementadas em `bauer/tools/factory.py`. Confirmado num deploy: `GET /status` do serve lista 12 tools, **sem** as do factory.

**(b) O serve não injeta contexto do factory.** O serve monta o prompt em `bauer/commands/serve_cmd.py:93-94`:

```python
    from ..agent import _build_system_prompt
    system_prompt = _build_system_prompt(router)
```

O CLI, por outro lado, computa o estado do factory (`bauer/agent.py:2924-2966`: itera `workspace`, chama `app_factory.is_governed`, `current_gate`, `delivery_score`) — mas isso vive no **banner interativo do CLI**, não em `_build_system_prompt`, então o serve não herda nada.

Funções disponíveis em `bauer/app_factory.py` (confirmadas):
- `get_active_project(workspace) -> Optional[Path]` (linha 185)
- `is_governed(project_dir) -> bool` (linha 146)
- `current_gate(project_dir) -> Optional[Gate]` (linha 331) — `Gate` tem `.slug` (ex.: "discovery", "planning", "implementation", "delivery")
- `missing_planning_docs(project_dir) -> List[str]` (linha 321)

O serve tem a `workspace` disponível na função `serve()` de `serve_cmd.py` (parâmetro `workspace`, usado em `_build_router(cfg, workspace)`).

Convenção: funções `_`-privadas; imports lazy dentro de funções são comuns no repo (ver `serve_cmd.py`); testes em `tests/` com pytest.

## Commands you will need

| Purpose   | Command                                              | Expected |
|-----------|------------------------------------------------------|----------|
| Sintaxe   | `python -c "import ast,glob; [ast.parse(open(f,encoding='utf-8').read()) for f in ['bauer/commands/_runtime.py','bauer/commands/serve_cmd.py','bauer/app_factory.py']]"` | sem erro |
| Testes    | `python -m pytest tests/test_app_factory_serve.py -q` | passam |
| Lint      | `python -m ruff check bauer/commands/_runtime.py bauer/commands/serve_cmd.py bauer/app_factory.py` | `All checks passed!` |

## Scope

**In scope**:
- `bauer/commands/_runtime.py` — adicionar as tools do factory ao `_LOCAL_DEFAULT_ALLOWLIST`
- `bauer/app_factory.py` — nova função `system_prompt_section(workspace) -> str`
- `bauer/commands/serve_cmd.py` — anexar a seção do factory ao system prompt
- `tests/test_app_factory_serve.py` (criar)

**Out of scope** (NÃO tocar):
- A lógica de gates (`can_write_code`, `current_gate`) — só **ler** o estado, nunca alterar as regras.
- As tools `app_factory_*` em `bauer/tools/factory.py` — já funcionam.
- O banner do CLI (`agent.py:2924`) — não refatorar; este plano não remove a duplicação, só cobre o serve.

## Git workflow

- Branch: `advisor/024-app-factory-serve`
- Conventional commits (ex.: `feat(serve): App Factory no Desktop — tools + contexto no prompt`)
- Sem push/PR salvo instrução.

## Steps

### Step 1: Expor as tools do factory no toolset local

Em `bauer/commands/_runtime.py`, adicione `"app_factory_init"` e `"app_factory_status"` ao `_LOCAL_DEFAULT_ALLOWLIST`:

```python
_LOCAL_DEFAULT_ALLOWLIST = [
    "read_file", "write_file", "list_dir", "run_command",
    "web_search", "web_fetch", "search_text", "glob_files",
    "datetime_now", "calculate", "memory", "todo",
    "app_factory_init", "app_factory_status",
]
```

**Verify**: `python -c "from bauer.commands._runtime import _LOCAL_DEFAULT_ALLOWLIST as a; assert 'app_factory_init' in a and 'app_factory_status' in a; print('ok')"` → `ok`

### Step 2: Criar `system_prompt_section` em app_factory.py

Adicione uma função que retorna um trecho de system prompt descrevendo o estado do factory. Deve **nunca levantar** (best-effort) e retornar `""` quando não há projeto governado. Molde:

```python
def system_prompt_section(workspace: "Path | str") -> str:
    """Trecho de system prompt com o estado da App Factory do projeto ativo.

    Vazio quando não há projeto governado — nesse caso o modelo age normalmente.
    Quando há, orienta o modelo a respeitar os gates do Spec-Driven Development.
    """
    try:
        proj = get_active_project(workspace)
        if proj is None or not is_governed(proj):
            return (
                "\n# APP FACTORY\n"
                "Nenhum projeto sob governança da App Factory está ativo. Se o usuário\n"
                "descrever uma ideia de aplicação NOVA, use a tool app_factory_init para\n"
                "iniciar o Spec-Driven Development (cria os docs e ativa os gates).\n"
            )
        gate = current_gate(proj)
        gate_slug = gate.slug if gate is not None else "desconhecido"
        missing = missing_planning_docs(proj)
        lines = [
            "\n# APP FACTORY (projeto governado ativo)\n",
            f"Projeto: {proj.name} | Gate atual: {gate_slug}\n",
            "Este projeto segue Spec-Driven Development com GATES. A escrita de código\n",
            "fica BLOQUEADA até o planejamento (7 docs) estar completo.\n",
        ]
        if missing:
            lines.append(f"Docs de planejamento pendentes: {', '.join(missing)}\n")
            lines.append(
                "Ajude o usuário a preencher esses docs (use write_file no diretório docs/),\n"
                "um de cada vez, ANTES de tentar escrever código. Consulte app_factory_status\n"
                "para ver o progresso.\n"
            )
        else:
            lines.append("Planejamento completo — a implementação está liberada.\n")
        return "".join(lines)
    except Exception:
        return ""
```

Garanta que `get_active_project`, `is_governed`, `current_gate`, `missing_planning_docs` estão no escopo do módulo (já estão — mesma `app_factory.py`).

**Verify**: `python -c "from bauer import app_factory; print(repr(app_factory.system_prompt_section('.')))"` → imprime uma string (vazia ou com `# APP FACTORY`), sem exceção

### Step 3: Anexar a seção ao system prompt do serve

Em `bauer/commands/serve_cmd.py`, logo após montar `system_prompt`:

```python
    from ..agent import _build_system_prompt
    system_prompt = _build_system_prompt(router)  # se o plano 023 já landou, passe tool_mode aqui
    try:
        from .. import app_factory as _af
        system_prompt += _af.system_prompt_section(workspace)
    except Exception:
        pass
```

**Verify**: `python -c "import ast; ast.parse(open('bauer/commands/serve_cmd.py',encoding='utf-8').read()); print('ok')"` → `ok`

### Step 4: Testes

Crie `tests/test_app_factory_serve.py`:
- `test_allowlist_includes_factory_tools` — `app_factory_init`/`status` estão em `_LOCAL_DEFAULT_ALLOWLIST`.
- `test_section_empty_when_not_governed` — `system_prompt_section(tmp_path)` retorna `""` ou o texto "Nenhum projeto sob governança" (num workspace tmp sem projeto).
- `test_section_never_raises` — chamar com um path inexistente não levanta.
- (Opcional, se viável montar um projeto governado com `app_factory.init_project`): `test_section_mentions_gate_and_missing_docs`.

Padrão: `tests/test_desktop_api.py` (uso de `tmp_path`, mocks simples).

**Verify**: `python -m pytest tests/test_app_factory_serve.py -q` → todos passam

## Test plan

- Novos testes em `tests/test_app_factory_serve.py` (acima).
- Verificação: `python -m pytest tests/test_app_factory_serve.py -q` → passam.

## Done criteria

- [ ] `app_factory_init`/`app_factory_status` em `_LOCAL_DEFAULT_ALLOWLIST`
- [ ] `app_factory.system_prompt_section` existe, é best-effort (não levanta), e retorna `""`/texto conforme governança
- [ ] `serve_cmd.py` anexa a seção ao system prompt
- [ ] `python -m pytest tests/test_app_factory_serve.py -q` passa
- [ ] `python -m ruff check` nos arquivos em escopo → `All checks passed!`
- [ ] Nenhum arquivo fora do escopo modificado (`git status`)
- [ ] Linha de status atualizada em `plans/README.md`

## STOP conditions

Pare e reporte se:
- As assinaturas de `get_active_project`/`current_gate`/`missing_planning_docs` divergirem dos excertos (a `app_factory.py` drifou).
- `serve()` em `serve_cmd.py` não tiver `workspace` no escopo onde o system prompt é montado.
- O `Gate` não tiver atributo `.slug` (confirme em `app_factory.py`, classe `Gate`).

## Maintenance notes

- Isto **não** aplica o gate no `write_file` do serve (o `can_write_code` já existe; ligar o gate no fluxo de escrita do serve é um follow-up separado e mais arriscado). Aqui o gate é comunicado ao modelo via prompt, não imposto no serve. Anote como próximo passo se o usuário quiser bloqueio duro.
- O banner do CLI (`agent.py:2924`) continua com a sua própria cópia da lógica de listagem — uma unificação futura poderia fazer ambos consumirem `system_prompt_section`.
- Revisor deve conferir: a seção só aparece quando há workspace válido e nunca quebra o boot do serve.

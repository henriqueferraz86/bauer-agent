"""App Factory — Spec-Driven Development gravado no DNA do Bauer.

Transforma uma ideia bruta em uma aplicação V1 funcional seguindo um processo
com *quality gates* obrigatórios. Diferente de uma skill (que é só orientação no
prompt), a App Factory é **governança executável**: quando um projeto está sob
governança, o :class:`ToolRouter` recusa escrever código antes da especificação
existir — o gate é obedecido pelo agent loop, não apenas sugerido.

Modelo de estado
----------------
A governança é *opt-in por projeto* e marcada por ``docs/.app_factory.json``.
Sem esse arquivo, nada muda no comportamento normal do Bauer.

Os gates são DERIVADOS do estado real dos documentos (não de um campo manual):

    DISCOVERY      → governado, mas SPEC ainda não preenchida
    PLANNING       → SPEC preenchida, faltam docs de planejamento
    IMPLEMENTATION → os 7 docs de planejamento preenchidos (libera código)
    DELIVERY       → entrega pronta (score objetivo >= limiar)

"Preenchido" = o documento existe E foi editado em relação ao esqueleto que o
scaffold gravou (comparação por hash). Isso impede burlar o gate só criando
esqueletos vazios.

Uso programático::

    from bauer import app_factory as af

    af.init_project(Path("meu-app"), idea="Encurtador de URLs", stack="FastAPI")
    af.current_gate(Path("meu-app"))           # Gate.PLANNING
    ok, motivo = af.can_write_code(Path("meu-app"), "app/main.py")
    af.delivery_score(Path("meu-app"))         # {"score": 6.0, "checks": {...}}
"""

from __future__ import annotations

import datetime as _dt
import enum
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent / "data" / "app_factory" / "templates"

#: Documentos de planejamento — todos exigidos antes de liberar a escrita de código.
PLANNING_DOCS: tuple[str, ...] = (
    "SPEC.md",
    "ARCHITECTURE.md",
    "BACKLOG.md",
    "TASKS.md",
    "DECISIONS.md",
    "PROJECT_CONTEXT.md",
    "PROGRESS.md",
)

#: Documentos de entrega/qualidade — scaffold cria, mas não bloqueiam o gate de código.
DELIVERY_DOCS: tuple[str, ...] = (
    "DEFINITION_OF_DONE.md",
    "SECURITY_CHECKLIST.md",
    "DEPLOY_CHECKLIST.md",
    "RUNBOOK.md",
    "DELIVERY_SCORE.md",
    "CHECKLIST_V1.md",
)

#: Arquivos de template gravados na RAIZ do projeto (não em docs/).
#: mapeia nome do template → caminho relativo de destino.
ROOT_FILES: Dict[str, str] = {
    "README.md": "README.md",
    ".env.example": ".env.example",
    "ci-github-actions.yml": ".github/workflows/ci.yml",
}

#: Nome do marker de governança (dentro de docs/).
MARKER_NAME = ".app_factory.json"

#: Caminhos sempre liberados para escrita mesmo antes do gate de implementação.
#: Docs/config/metodologia podem (e devem) ser escritos durante o planejamento.
_ALWAYS_WRITABLE_PREFIXES = ("docs/", ".github/")
_ALWAYS_WRITABLE_FILES = {"README.md", ".env.example", ".gitignore", ".env"}

#: Limiar do Delivery Score para considerar a V1 "pronta".
DELIVERY_READY_THRESHOLD = 8.0

#: Tamanho mínimo (chars) para um doc sem hash pristino contar como preenchido.
_MIN_FILLED_CHARS = 120


class Gate(enum.IntEnum):
    """Estágios da App Factory. Ordenável: ``Gate.PLANNING < Gate.IMPLEMENTATION``."""

    DISCOVERY = 0
    PLANNING = 1
    IMPLEMENTATION = 2
    DELIVERY = 3

    @property
    def slug(self) -> str:
        return self.name.lower()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def templates_dir() -> Path:
    """Diretório dos templates embutidos."""
    return _TEMPLATES_DIR


def list_templates() -> List[str]:
    """Nomes de todos os templates disponíveis."""
    if not _TEMPLATES_DIR.is_dir():
        return []
    return sorted(p.name for p in _TEMPLATES_DIR.iterdir() if p.is_file())


def load_template(name: str) -> str:
    """Carrega o conteúdo de um template pelo nome de arquivo.

    Raises:
        FileNotFoundError: se o template não existir.
    """
    path = _TEMPLATES_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"template '{name}' nao encontrado em {_TEMPLATES_DIR}")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Marker / estado
# ---------------------------------------------------------------------------


def marker_path(project_dir: Path | str) -> Path:
    """Caminho do marker de governança (``docs/.app_factory.json``)."""
    return Path(project_dir) / "docs" / MARKER_NAME


def is_governed(project_dir: Path | str) -> bool:
    """True se o projeto está sob governança da App Factory."""
    return marker_path(project_dir).is_file()


# ---------------------------------------------------------------------------
# Projeto ativo + containment (1 ideia = 1 pasta; nada solto na raiz)
# ---------------------------------------------------------------------------

#: Ponteiro do projeto ativo, por workspace. Guarda a pasta da ideia em foco
#: para que a escrita de arquivos fique contida nela (não vaze para a raiz nem
#: para pastas irmãs). Criado pelo app_factory_init; ausente = sem containment.
_ACTIVE_POINTER_REL = Path(".bauer_meta") / "app_factory_active.json"


def _active_pointer_path(workspace: Path | str) -> Path:
    return Path(workspace) / _ACTIVE_POINTER_REL


def set_active_project(workspace: Path | str, project_dir: Path | str) -> None:
    """Marca ``project_dir`` como o projeto ativo do workspace.

    A escrita subsequente de código/arquivos fica contida nessa pasta.
    Guarda o caminho RELATIVO ao workspace quando possível (portável).
    """
    ws = Path(workspace).resolve()
    pd = Path(project_dir).resolve()
    try:
        rel = pd.relative_to(ws).as_posix()
    except ValueError:
        rel = pd.as_posix()  # fora do workspace — guarda absoluto
    ptr = _active_pointer_path(ws)
    ptr.parent.mkdir(parents=True, exist_ok=True)
    ptr.write_text(
        json.dumps({"project": rel}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_active_project(workspace: Path | str) -> Optional[Path]:
    """Retorna a pasta do projeto ativo (absoluta) ou ``None``.

    Defensivo: se o ponteiro aponta para uma pasta inexistente, retorna None
    (não força containment em um alvo morto).
    """
    ptr = _active_pointer_path(workspace)
    if not ptr.is_file():
        return None
    try:
        data = json.loads(ptr.read_text(encoding="utf-8"))
        rel = str(data.get("project", "")).strip()
    except (json.JSONDecodeError, OSError):
        return None
    if not rel:
        return None
    cand = Path(rel)
    if not cand.is_absolute():
        cand = Path(workspace) / cand
    return cand if cand.exists() else None


def path_is_within(project_dir: Path | str, workspace: Path | str, target: str) -> bool:
    """True se ``target`` (relativo ao workspace ou absoluto) cai DENTRO de project_dir."""
    base = Path(project_dir).resolve()
    t = Path(target)
    if not t.is_absolute():
        t = Path(workspace) / t
    try:
        t.resolve().relative_to(base)
        return True
    except (ValueError, OSError):
        return False


def check_containment(
    workspace: Path | str, target: str
) -> Tuple[bool, str]:
    """Garante que ``target`` fique dentro do projeto ativo (se houver).

    Returns:
        ``(permitido, motivo)``. Sem projeto ativo → sempre permitido (no-op).
        Alvo fora do projeto ativo → bloqueado, com orientação de onde escrever.
    """
    active = get_active_project(workspace)
    if active is None:
        return True, ""
    if path_is_within(active, workspace, target):
        return True, ""
    try:
        name = active.relative_to(Path(workspace).resolve()).as_posix()
    except (ValueError, OSError):
        name = active.name
    rel_target = str(target).replace("\\", "/").lstrip("/")
    return False, (
        f"App Factory: projeto ativo é '{name}/'. Tudo deste projeto deve ficar "
        f"DENTRO dessa pasta — nada solto na raiz nem em pastas irmãs.\n"
        f"  Você tentou escrever em: '{rel_target or target}'\n"
        f"  Escreva sob '{name}/' — ex.: '{name}/app/...', '{name}/frontend/...', "
        f"'{name}/{rel_target.split('/', 1)[-1] if '/' in rel_target else rel_target}'."
    )


def load_state(project_dir: Path | str) -> Optional[Dict[str, Any]]:
    """Lê o estado do marker. Retorna ``None`` se não governado/ilegível."""
    p = marker_path(project_dir)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_state(project_dir: Path | str, state: Dict[str, Any]) -> None:
    """Persiste o estado no marker (cria docs/ se preciso)."""
    p = marker_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return _dt.date.today().isoformat()


# ---------------------------------------------------------------------------
# Preenchimento de documentos
# ---------------------------------------------------------------------------


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _doc_path(project_dir: Path | str, name: str) -> Path:
    return Path(project_dir) / "docs" / name


def doc_is_filled(project_dir: Path | str, name: str) -> bool:
    """True se o documento existe E foi preenchido (difere do esqueleto).

    Critério: se o scaffold gravou um hash pristino, o doc conta como
    preenchido apenas quando seu conteúdo atual difere desse hash. Sem hash
    pristino (doc criado manualmente), conta se tiver conteúdo não-trivial.
    """
    path = _doc_path(project_dir, name)
    if not path.is_file():
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False

    state = load_state(project_dir) or {}
    pristine = (state.get("pristine_hashes") or {}).get(name)
    if pristine:
        return _hash_text(content) != pristine
    return len(content.strip()) >= _MIN_FILLED_CHARS


def planning_complete(project_dir: Path | str) -> bool:
    """True quando todos os documentos de planejamento estão preenchidos."""
    return all(doc_is_filled(project_dir, d) for d in PLANNING_DOCS)


def _spec_filled(project_dir: Path | str) -> bool:
    return doc_is_filled(project_dir, "SPEC.md")


def missing_planning_docs(project_dir: Path | str) -> List[str]:
    """Documentos de planejamento ainda não preenchidos."""
    return [d for d in PLANNING_DOCS if not doc_is_filled(project_dir, d)]


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def current_gate(project_dir: Path | str) -> Optional[Gate]:
    """Gate atual derivado do estado dos documentos.

    Retorna ``None`` quando o projeto não está sob governança.
    """
    if not is_governed(project_dir):
        return None
    if planning_complete(project_dir):
        score_data = delivery_score(project_dir)
        # Gate.DELIVERY exige score >= limiar E smoke run verde (gate hard).
        # "arquivos presentes" não basta — o app tem que rodar de verdade.
        if score_data["ready"] and _last_verify_ok(project_dir):
            return Gate.DELIVERY
        return Gate.IMPLEMENTATION
    if _spec_filled(project_dir):
        return Gate.PLANNING
    return Gate.DISCOVERY


def _rel_posix(project_dir: Path | str, target: str) -> str:
    """Normaliza ``target`` para caminho relativo ao projeto em formato posix."""
    root = Path(project_dir).resolve()
    t = Path(target)
    if not t.is_absolute():
        t = (root / t)
    try:
        rel = t.resolve().relative_to(root)
    except (ValueError, OSError):
        # fora da raiz — usa o caminho como veio, normalizado
        return str(t).replace("\\", "/").lstrip("/")
    return rel.as_posix()


def can_write_code(project_dir: Path | str, target: str) -> Tuple[bool, str]:
    """Decide se a escrita em ``target`` é permitida pelo gate atual.

    Regras (só valem para projetos governados):
      - docs/, .github/, README, .env.example, .gitignore → sempre liberados
      - gate >= IMPLEMENTATION → tudo liberado
      - caso contrário → bloqueado (precisa terminar o planejamento antes)

    Returns:
        ``(permitido, motivo)``. ``motivo`` é vazio quando permitido.
    """
    if not is_governed(project_dir):
        return True, ""

    rel = _rel_posix(project_dir, target)
    if rel in _ALWAYS_WRITABLE_FILES:
        return True, ""
    if any(rel.startswith(pfx) for pfx in _ALWAYS_WRITABLE_PREFIXES):
        return True, ""

    gate = current_gate(project_dir)
    if gate is not None and gate >= Gate.IMPLEMENTATION:
        return True, ""

    missing = missing_planning_docs(project_dir)
    current_gate_slug = gate.slug if gate else "discovery"
    if current_gate_slug == "discovery":
        gate_hint = (
            "  DISCOVERY: use a tool 'clarify' para perguntar ao usuario sobre\n"
            "  usuarios-alvo, funcionalidades V1, stack e criterio de sucesso.\n"
            "  Depois preencha os docs em docs/ com o que foi coletado."
        )
    else:
        gate_hint = (
            f"  Documentos pendentes em docs/: {', '.join(missing) or '(nenhum)'}\n"
            "  Preencha-os (use write_file em docs/<NOME>.md)."
        )
    reason = (
        "App Factory: escrita de codigo bloqueada — o planejamento ainda nao "
        f"esta completo (gate atual: {current_gate_slug}).\n"
        f"{gate_hint}"
    )
    return False, reason


# ---------------------------------------------------------------------------
# Scaffold
# ---------------------------------------------------------------------------


def _personalize(name: str, content: str, *, idea: str, stack: str, project_name: str) -> str:
    """Substituições leves de placeholders nos esqueletos."""
    out = content.replace("YYYY-MM-DD", _today())
    if name == "SPEC.md" and idea:
        out = out.replace(
            "Descreva a aplicação em poucas linhas.",
            idea.strip(),
        )
    if name == "PROJECT_CONTEXT.md":
        if project_name:
            out = out.replace("[Nome]", project_name)
        if idea:
            out = out.replace("[Objetivo principal]", idea.strip())
    return out


def scaffold_docs(
    project_dir: Path | str,
    *,
    idea: str = "",
    stack: str = "",
    overwrite: bool = False,
) -> List[str]:
    """Grava os esqueletos de documentos e arquivos-raiz a partir dos templates.

    Registra o hash pristino de cada doc de planejamento no marker, para que o
    gate saiba quando o agente realmente editou (preencheu) o documento.

    Args:
        idea: descrição da ideia (injetada na SPEC/PROJECT_CONTEXT).
        stack: stack preferida (registrada no estado).
        overwrite: se False, não sobrescreve arquivos já existentes.

    Returns:
        Lista de caminhos relativos efetivamente escritos.
    """
    root = Path(project_dir)
    project_name = root.resolve().name
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    written: List[str] = []
    pristine: Dict[str, str] = {}

    # Documentos em docs/
    for name in (*PLANNING_DOCS, *DELIVERY_DOCS):
        try:
            tpl = load_template(name)
        except FileNotFoundError:
            continue
        body = _personalize(name, tpl, idea=idea, stack=stack, project_name=project_name)
        dest = docs_dir / name
        # Hash pristino de TODO doc scaffoldado (planejamento e entrega): assim
        # doc_is_filled() — e o Delivery Score — só contam quando o agente
        # realmente editou o documento, não pelo mero esqueleto.
        pristine[name] = _hash_text(body)
        if dest.exists() and not overwrite:
            continue
        dest.write_text(body, encoding="utf-8")
        written.append(f"docs/{name}")

    # Arquivos na raiz
    for tpl_name, rel_dest in ROOT_FILES.items():
        try:
            tpl = load_template(tpl_name)
        except FileNotFoundError:
            continue
        dest = root / rel_dest
        if dest.exists() and not overwrite:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(tpl, encoding="utf-8")
        written.append(rel_dest)

    # Atualiza marker preservando estado anterior
    state = load_state(project_dir) or {}
    state.setdefault("idea", idea)
    state.setdefault("created_at", _now_iso())
    if stack:
        state["stack"] = stack
    # mescla hashes pristinos (não apaga os de docs não reescritos)
    merged = dict(state.get("pristine_hashes") or {})
    merged.update(pristine)
    state["pristine_hashes"] = merged
    state["updated_at"] = _now_iso()
    save_state(project_dir, state)

    return written


def init_project(
    project_dir: Path | str,
    *,
    idea: str = "",
    stack: str = "",
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Inicia a governança da App Factory num projeto e cria os esqueletos.

    Returns:
        Estado resultante (dict do marker) acrescido de ``written`` e ``gate``.
    """
    written = scaffold_docs(project_dir, idea=idea, stack=stack, overwrite=overwrite)
    state = load_state(project_dir) or {}
    gate = current_gate(project_dir)
    return {
        **state,
        "written": written,
        "gate": gate.slug if gate is not None else "discovery",
    }


# ---------------------------------------------------------------------------
# Delivery Score (objetivo, automatizado)
# ---------------------------------------------------------------------------


def _has_tests(project_dir: Path | str) -> bool:
    root = Path(project_dir)
    tests_dir = root / "tests"
    if tests_dir.is_dir() and any(tests_dir.rglob("test_*.py")):
        return True
    if any(root.rglob("test_*.py")):
        return True
    # JS/TS
    for pat in ("*.test.js", "*.test.ts", "*.spec.ts", "*.spec.js"):
        if any(root.rglob(pat)):
            return True
    return False


def _root_file_ok(project_dir: Path | str, rel: str, min_chars: int = 1) -> bool:
    p = Path(project_dir) / rel
    if not p.is_file():
        return False
    try:
        return len(p.read_text(encoding="utf-8").strip()) >= min_chars
    except OSError:
        return False


def _last_verify_ok(project_dir: Path | str) -> bool:
    """True se a última execução de verify_app passou (lê .bauer_meta/verify_result.json)."""
    import json as _json
    p = Path(project_dir) / ".bauer_meta" / "verify_result.json"
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
        return bool(data.get("ok"))
    except Exception:
        return False


def delivery_score(project_dir: Path | str) -> Dict[str, Any]:
    """Calcula um Delivery Score objetivo (0–10) a partir de sinais verificáveis.

    Cada item vale igual; o score é a fração satisfeita × 10. ``ready`` indica
    se atingiu o limiar de prontidão da V1.

    P1.4: o check ``verified`` exige que verify_app tenha passado (build/test
    verde) — "arquivo existe" não basta, o app tem que rodar de verdade.
    """
    checks: Dict[str, bool] = {
        "spec": doc_is_filled(project_dir, "SPEC.md"),
        "architecture": doc_is_filled(project_dir, "ARCHITECTURE.md"),
        "backlog": doc_is_filled(project_dir, "BACKLOG.md"),
        "progress": doc_is_filled(project_dir, "PROGRESS.md"),
        "security": doc_is_filled(project_dir, "SECURITY_CHECKLIST.md"),
        "deploy": doc_is_filled(project_dir, "DEPLOY_CHECKLIST.md"),
        "runbook": doc_is_filled(project_dir, "RUNBOOK.md"),
        "readme": _root_file_ok(project_dir, "README.md", min_chars=80),
        "env_example": _root_file_ok(project_dir, ".env.example"),
        "tests": _has_tests(project_dir),
        "verified": _last_verify_ok(project_dir),  # P1.4: app roda de verdade
    }
    total = len(checks)
    satisfied = sum(1 for v in checks.values() if v)
    score = round(10.0 * satisfied / total, 1) if total else 0.0
    return {
        "score": score,
        "checks": checks,
        "satisfied": satisfied,
        "total": total,
        "ready": score >= DELIVERY_READY_THRESHOLD,
    }


# ---------------------------------------------------------------------------
# Status agregado
# ---------------------------------------------------------------------------


def status(project_dir: Path | str) -> Dict[str, Any]:
    """Resumo do estado da App Factory para CLI/tool/Desktop."""
    governed = is_governed(project_dir)
    gate = current_gate(project_dir)
    score = delivery_score(project_dir) if governed else None
    return {
        "governed": governed,
        "gate": gate.slug if gate is not None else None,
        "planning_complete": planning_complete(project_dir) if governed else False,
        "missing_planning_docs": missing_planning_docs(project_dir) if governed else [],
        "delivery_score": score,
        "state": load_state(project_dir),
    }

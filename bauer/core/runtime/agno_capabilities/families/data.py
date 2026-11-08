"""Sprint 52 — Bauer Data: análise local de CSV/JSON (plano 057, sprint "20").

``bauer.data`` analyses tabular files **inside the agent's workspace** with
the standard library (no pandas requirement), under explicit budgets:
file size, rows scanned, wall time and rows returned. Reports and charts are
written only to ``<workspace>/.bauer/artifacts/data/`` — source files are
never modified and nothing is uploaded.

SDK toolkits that evaluate arbitrary dataframe operations or read paths
outside the workspace (``agno.pandas``, ``agno.csv_toolkit``,
``agno.visualization``) are catalogued as incompatible in favour of this one.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

from pydantic import Field

from .....tools.base import ToolError
from ..bridge import ActionImpl, register_factory
from ..catalog import enable_approval_flow
from ..manifest import ActionPolicy, ToolkitPolicy, register_policy
from ..settings import StrictSettings, register_settings_model

_EXTENSIONS = {".csv", ".tsv", ".json", ".jsonl", ".ndjson"}
_ARTIFACT_DIR = Path(".bauer") / "artifacts" / "data"
_OPS = {"eq", "ne", "lt", "le", "gt", "ge", "contains", "startswith", "isnull", "notnull"}
_AGGS = {"count", "sum", "mean", "min", "max"}


class DataSettings(StrictSettings):
    max_file_bytes: int = Field(default=50_000_000, ge=1_000, le=2_000_000_000)
    max_rows_scanned: int = Field(default=200_000, ge=100, le=10_000_000)
    max_seconds: float = Field(default=15.0, gt=0, le=300)
    max_result_rows: int = Field(default=200, ge=1, le=5_000)
    max_artifact_bytes: int = Field(default=2_000_000, ge=1_000, le=50_000_000)


class _Budget:
    def __init__(self, settings: DataSettings) -> None:
        self.settings = settings
        self.deadline = time.monotonic() + settings.max_seconds
        self.scanned = 0
        self.truncated = False

    def tick(self) -> bool:
        """Return False when the scan must stop (row budget); raise on time."""
        self.scanned += 1
        if self.scanned % 1_000 == 0 and time.monotonic() > self.deadline:
            raise ToolError(f"análise excedeu o orçamento de tempo ({self.settings.max_seconds:g}s)")
        if self.scanned > self.settings.max_rows_scanned:
            self.truncated = True
            return False
        return True


def _resolve(workspace: Path, path: str, settings: DataSettings) -> Path:
    raw = str(path or "").strip()
    if not raw:
        raise ToolError("path é obrigatório")
    candidate = Path(raw)
    target = (candidate if candidate.is_absolute() else workspace / candidate).resolve()
    root = workspace.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ToolError("arquivo fora do workspace autorizado") from None
    if target.suffix.lower() not in _EXTENSIONS:
        raise ToolError(f"extensão não suportada; use {', '.join(sorted(_EXTENSIONS))}")
    if not target.is_file():
        raise ToolError("arquivo não encontrado")
    if target.stat().st_size > settings.max_file_bytes:
        raise ToolError(f"arquivo excede o limite de {settings.max_file_bytes} bytes")
    return target


def _rows(target: Path, budget: _Budget) -> Iterator[dict[str, Any]]:
    suffix = target.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t" if suffix == ".tsv" else ",")
            for row in reader:
                if not budget.tick():
                    return
                yield {str(k): _coerce(v) for k, v in row.items() if k is not None}
    elif suffix in {".jsonl", ".ndjson"}:
        with target.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                if not budget.tick():
                    return
                try:
                    item = json.loads(line)
                except ValueError:
                    raise ToolError("linha JSONL inválida") from None
                if isinstance(item, dict):
                    yield item
    else:
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except ValueError:
            raise ToolError("JSON inválido") from None
        if isinstance(payload, dict):
            payload = next((value for value in payload.values() if isinstance(value, list)), [payload])
        if not isinstance(payload, list):
            raise ToolError("JSON deve conter uma lista de objetos")
        for item in payload:
            if not budget.tick():
                return
            if isinstance(item, dict):
                yield item


def _coerce(value: Any) -> Any:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if re.fullmatch(r"[-+]?\d+", text):
        try:
            return int(text)
        except ValueError:
            return text
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", text):
        try:
            number = float(text)
            return number if math.isfinite(number) else text
        except ValueError:
            return text
    return text


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, (dict, list)):
        return "object"
    return "str"


def _match(row: dict[str, Any], condition: dict[str, Any]) -> bool:
    column = str(condition.get("column") or "")
    op = str(condition.get("op") or "eq")
    if op not in _OPS:
        raise ToolError(f"operador inválido; use {', '.join(sorted(_OPS))}")
    value = row.get(column)
    expected: Any = condition.get("value")
    if op == "isnull":
        return value is None
    if op == "notnull":
        return value is not None
    if value is None:
        return False
    try:
        if op == "eq":
            return value == expected or str(value) == str(expected)
        if op == "ne":
            return not (value == expected or str(value) == str(expected))
        if op == "contains":
            return str(expected).lower() in str(value).lower()
        if op == "startswith":
            return str(value).lower().startswith(str(expected).lower())
        pair: tuple[Any, Any] = (value, expected) if isinstance(value, str) else (float(value), float(expected))
        left, right = pair
        return {"lt": left < right, "le": left <= right, "gt": left > right, "ge": left >= right}[op]
    except (TypeError, ValueError):
        return False


def _safe_name(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_\-]+", "-", str(name or "").strip()).strip("-").lower()[:80]
    if not slug:
        raise ToolError("nome de artefato inválido")
    return slug


def _actions(workspace: Path, settings: DataSettings) -> dict[str, Any]:
    def describe_file(path: str, sample_rows: int = 5) -> dict[str, Any]:
        """Descreve um CSV/TSV/JSON/JSONL do workspace: colunas, tipos, contagem e amostra.

        Args:
            path: Caminho relativo ao workspace.
            sample_rows: Linhas de amostra (máx. 20).
        """
        target = _resolve(workspace, path, settings)
        budget = _Budget(settings)
        types: dict[str, set[str]] = defaultdict(set)
        nulls: dict[str, int] = defaultdict(int)
        sample: list[dict[str, Any]] = []
        count = 0
        for row in _rows(target, budget):
            count += 1
            if len(sample) < max(0, min(int(sample_rows), 20)):
                sample.append(row)
            for key, value in row.items():
                kind = _type_name(value)
                if kind == "null":
                    nulls[key] += 1
                else:
                    types[key].add(kind)
        columns = sorted(set(types) | set(nulls))
        return {
            "path": str(target.relative_to(workspace.resolve())),
            "rows_scanned": count,
            "truncated": budget.truncated,
            "columns": [
                {"name": name, "types": sorted(types.get(name, set())) or ["null"], "nulls": nulls.get(name, 0)}
                for name in columns
            ],
            "sample": sample,
        }

    def query_file(
        path: str,
        columns: list[str] | None = None,
        where: list[dict[str, Any]] | None = None,
        group_by: list[str] | None = None,
        aggregate: dict[str, str] | None = None,
        order_by: str = "",
        descending: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Filtra, agrupa e agrega um arquivo tabular do workspace (sem alterar o arquivo).

        Args:
            path: Caminho relativo ao workspace.
            columns: Colunas a retornar (sem agregação).
            where: Lista de condições {column, op, value}; op em eq, ne, lt, le, gt, ge, contains, startswith, isnull, notnull.
            group_by: Colunas de agrupamento.
            aggregate: Mapa coluna→função (count, sum, mean, min, max).
            order_by: Coluna de ordenação do resultado.
            descending: Ordem decrescente.
            limit: Máximo de linhas retornadas.
        """
        target = _resolve(workspace, path, settings)
        budget = _Budget(settings)
        conditions = list(where or [])
        aggregates = dict(aggregate or {})
        for fn in aggregates.values():
            if fn not in _AGGS:
                raise ToolError(f"agregação inválida; use {', '.join(sorted(_AGGS))}")
        keys = list(group_by or [])
        cap = max(1, min(int(limit), settings.max_result_rows))
        if keys or aggregates:
            groups: dict[tuple, dict[str, Any]] = {}
            for row in _rows(target, budget):
                if not all(_match(row, c) for c in conditions):
                    continue
                key = tuple(row.get(k) for k in keys)
                state = groups.setdefault(key, {"__count": 0, **{c: [] for c in aggregates}})
                state["__count"] += 1
                for column in aggregates:
                    value = row.get(column)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        state[column].append(value)
            result_rows = []
            for key, state in groups.items():
                item = dict(zip(keys, key))
                for column, fn in aggregates.items():
                    values = state[column]
                    if fn == "count":
                        item[f"{fn}_{column}"] = state["__count"]
                    elif not values:
                        item[f"{fn}_{column}"] = None
                    elif fn == "sum":
                        item[f"{fn}_{column}"] = sum(values)
                    elif fn == "mean":
                        item[f"{fn}_{column}"] = sum(values) / len(values)
                    elif fn == "min":
                        item[f"{fn}_{column}"] = min(values)
                    else:
                        item[f"{fn}_{column}"] = max(values)
                if not aggregates:
                    item["count"] = state["__count"]
                result_rows.append(item)
        else:
            result_rows = []
            for row in _rows(target, budget):
                if all(_match(row, c) for c in conditions):
                    result_rows.append({k: row.get(k) for k in columns} if columns else row)
                    if not order_by and len(result_rows) > cap:
                        break
        if order_by:
            result_rows.sort(
                key=lambda item: (item.get(order_by) is None, str(type(item.get(order_by))), item.get(order_by)),
                reverse=bool(descending),
            )
        total = len(result_rows)
        return {
            "rows": result_rows[:cap],
            "returned": min(total, cap),
            "result_truncated": total > cap,
            "rows_scanned": budget.scanned if not budget.truncated else settings.max_rows_scanned,
            "scan_truncated": budget.truncated,
        }

    def save_report(name: str, content: str, format: str = "md") -> dict[str, Any]:
        """Salva relatório (md/csv/json) como artefato local em .bauer/artifacts/data/.

        Args:
            name: Nome do artefato (vira slug de arquivo).
            content: Conteúdo textual do relatório.
            format: md, csv ou json.
        """
        if format not in {"md", "csv", "json"}:
            raise ToolError("format deve ser md, csv ou json")
        data = str(content or "")
        if len(data.encode("utf-8")) > settings.max_artifact_bytes:
            raise ToolError("artefato excede o limite configurado")
        return _write_artifact(workspace, _safe_name(name), format, data)

    def save_chart(name: str, labels: list[str], values: list[float], title: str = "") -> dict[str, Any]:
        """Gera um gráfico de barras SVG local em .bauer/artifacts/data/ (sem upload).

        Args:
            name: Nome do artefato.
            labels: Rótulos das barras (máx. 50).
            values: Valores numéricos (mesmo tamanho de labels).
            title: Título opcional.
        """
        if not labels or len(labels) != len(values) or len(labels) > 50:
            raise ToolError("labels e values devem ter o mesmo tamanho (1..50)")
        try:
            numbers = [float(v) for v in values]
        except (TypeError, ValueError):
            raise ToolError("values deve conter números") from None
        return _write_artifact(workspace, _safe_name(name), "svg", _bar_svg(labels, numbers, title))

    return {
        "describe_file": describe_file,
        "query_file": query_file,
        "save_report": save_report,
        "save_chart": save_chart,
    }


def _write_artifact(workspace: Path, slug: str, ext: str, content: str) -> dict[str, Any]:
    root = (workspace / _ARTIFACT_DIR).resolve()
    try:
        root.relative_to(workspace.resolve())
    except ValueError:  # pragma: no cover - symlinked .bauer escaping the workspace
        raise ToolError("diretório de artefatos fora do workspace") from None
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{slug}.{ext}"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
    return {"artifact": str(target.relative_to(workspace.resolve())).replace("\\", "/"), "bytes": len(content.encode("utf-8"))}


def _escape(text: str) -> str:
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _bar_svg(labels: list[str], values: list[float], title: str) -> str:
    width, bar_h, gap, left = 640, 22, 6, 160
    height = 40 + len(values) * (bar_h + gap)
    peak = max((abs(v) for v in values), default=1.0) or 1.0
    out = io.StringIO()
    out.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="sans-serif" font-size="12">')
    out.write(f'<text x="8" y="20" font-size="14">{_escape(title)}</text>')
    for index, (label, value) in enumerate(zip(labels, values)):
        y = 32 + index * (bar_h + gap)
        length = int((width - left - 60) * abs(value) / peak)
        out.write(f'<text x="8" y="{y + 15}">{_escape(str(label)[:24])}</text>')
        out.write(f'<rect x="{left}" y="{y}" width="{length}" height="{bar_h}" fill="#4c78a8"/>')
        out.write(f'<text x="{left + length + 4}" y="{y + 15}">{value:g}</text>')
    out.write("</svg>")
    return out.getvalue()


def _factory(resolved, section, context=None) -> dict[str, ActionImpl]:
    from ..bridge import _parameters_for

    workspace = getattr(context, "workspace", None)
    if workspace is None:
        raise ToolError("bauer.data exige o workspace do agente")
    settings = section if isinstance(section, DataSettings) else DataSettings()
    actions = _actions(Path(workspace), settings)
    return {
        action.name: ActionImpl(
            fn=actions[action.name],
            description=(actions[action.name].__doc__ or "").strip().split("\n\n")[0],
            parameters=_parameters_for(actions[action.name]),
            timeout_seconds=int(settings.max_seconds) + 10,
        )
        for action in resolved.actions
    }


register_settings_model("bauer.data", DataSettings)
register_policy(
    ToolkitPolicy(
        id="bauer.data",
        title="Bauer Data (CSV/JSON local)",
        category="Dados",
        description="Descreve, filtra e agrega arquivos tabulares do workspace; relatórios/gráficos como artefatos locais.",
        source="bauer",
        actions=(
            ActionPolicy("describe_file", "read", "filesystem.read"),
            ActionPolicy("query_file", "read", "filesystem.read"),
            ActionPolicy("save_report", "write", "filesystem.write", approval_required=True),
            ActionPolicy("save_chart", "write", "filesystem.write", approval_required=True),
        ),
        notes="Escrita só em .bauer/artifacts/data/ do workspace, sob a policy filesystem.write (a mesma de bauer.write_file).",
    )
)
register_factory("bauer.data", _factory)
# Local artifacts: governed by the same PolicyEngine operation as write_file.
enable_approval_flow("bauer.data", "save_report")
enable_approval_flow("bauer.data", "save_chart")

for _capability_id, _title, _reason in (
    ("agno.pandas", "PandasTools", "executa operações arbitrárias de DataFrame por nome e lê caminhos sem escopo"),
    ("agno.csv_toolkit", "CsvTools", "lê CSVs de caminhos livres e consulta via DuckDB sem orçamento"),
    ("agno.visualization", "VisualizationTools", "grava gráficos em diretório arbitrário via matplotlib"),
):
    register_policy(
        ToolkitPolicy(
            id=_capability_id,
            title=_title,
            category="Dados",
            description="Toolkit de análise de dados do SDK Agno.",
            incompatible=f"O toolkit {_reason}. Use bauer.data (workspace isolado, orçamentos, artefatos locais).",
        )
    )

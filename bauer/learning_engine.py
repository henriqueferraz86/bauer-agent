"""Motor de aprendizado do Bauer Agent (Fases 7 e 8).

Regras fundamentais (Premortem item 7):
- Aprendizado auditavel: toda recomendacao tem motivo e evidencia.
- Aprendizado reversivel: bauer learning reset cria .bak e limpa arquivos.
- Nunca altera config automaticamente — so sugere.
- Nunca cria skill executavel.
- Toda recomendacao mostra o motivo. Nada de decisao oculta.

v2 (Fase 8): LearningEngineV2 usa o modelo configurado para analisar
os arquivos de memoria e gerar insights em linguagem natural.
Comando: bauer learning analyze
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .memory_manager import MemoryManager

_SECTION_RE = re.compile(r"^## \[([^\]]+)\] (.+)$", re.MULTILINE)
_FIELD_RE = re.compile(r"^- (\w+):\s*(.+)$", re.MULTILINE)


@dataclass
class ModelExp:
    timestamp: str
    title: str
    context_tokens: int = 0
    result: str = ""
    ram_used_mb: int = 0
    machine_id: str = ""
    lesson: str = ""


@dataclass
class FailedAttempt:
    timestamp: str
    title: str
    error: str = ""
    fix: str = ""
    machine_id: str = ""


@dataclass
class Recommendation:
    action: str
    reason: str
    evidence: list[str] = field(default_factory=list)
    severity: str = "info"  # info | suggestion | warning


def _parse_sections(text: str) -> list[tuple[str, str, dict[str, str]]]:
    """Extrai (timestamp, title, fields) de cada secao ## [...] no texto."""
    sections = []
    matches = list(_SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        ts = m.group(1).strip()
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        fields = dict(_FIELD_RE.findall(body))
        sections.append((ts, title, fields))
    return sections


def parse_model_experience(text: str) -> list[ModelExp]:
    """Parseia MODEL_EXPERIENCE.md e retorna lista de ModelExp."""
    exps = []
    for ts, title, fields in _parse_sections(text):
        try:
            ctx = int(fields.get("context_tokens", 0))
        except ValueError:
            ctx = 0
        try:
            ram = int(fields.get("ram_used_mb", 0))
        except ValueError:
            ram = 0
        exps.append(ModelExp(
            timestamp=ts,
            title=title,
            context_tokens=ctx,
            result=fields.get("result", ""),
            ram_used_mb=ram,
            machine_id=fields.get("machine_id", ""),
            lesson=fields.get("lesson", ""),
        ))
    return exps


def parse_failed_attempts(text: str) -> list[FailedAttempt]:
    """Parseia FAILED_ATTEMPTS.md e retorna lista de FailedAttempt."""
    attempts = []
    for ts, title, fields in _parse_sections(text):
        attempts.append(FailedAttempt(
            timestamp=ts,
            title=title,
            error=fields.get("error", ""),
            fix=fields.get("fix", ""),
            machine_id=fields.get("machine_id", ""),
        ))
    return attempts


def generate_recommendations(
    exps: list[ModelExp],
    failures: list[FailedAttempt],
    machine_id: str = "",
) -> list[Recommendation]:
    """Gera recomendacoes baseadas em regras. Cada uma tem motivo e evidencia.

    Nunca altera config. Nunca executa nada. So sugere.
    """
    recs: list[Recommendation] = []

    # Filtra por machine_id quando fornecido (entradas sem machine_id passam)
    if machine_id:
        relevant_exps = [e for e in exps if not e.machine_id or e.machine_id == machine_id]
        relevant_failures = [f for f in failures if not f.machine_id or f.machine_id == machine_id]
    else:
        relevant_exps = exps
        relevant_failures = failures

    # Regra 1: OOM detectado
    oom_exps = [
        e for e in relevant_exps
        if "oom" in e.result.lower() or "out of memory" in e.result.lower()
    ]
    if oom_exps:
        min_oom_ctx = min((e.context_tokens for e in oom_exps if e.context_tokens), default=0)
        evidence = [f"{e.title} [{e.timestamp}]: result={e.result}" for e in oom_exps[:3]]
        action = (
            f"Reduza o contexto abaixo de {min_oom_ctx} tokens para evitar OOM"
            if min_oom_ctx
            else "Reduza o contexto — falhas OOM detectadas"
        )
        recs.append(Recommendation(
            action=action,
            reason=f"Detectado {len(oom_exps)} falha(s) de memoria (OOM) nos registros",
            evidence=evidence,
            severity="warning",
        ))

    # Regra 2: Execucoes lentas
    slow_exps = [e for e in relevant_exps if "slow" in e.result.lower()]
    if slow_exps:
        evidence = [f"{e.title} [{e.timestamp}]: result=slow" for e in slow_exps[:3]]
        recs.append(Recommendation(
            action="Considere reduzir o contexto ou usar um modelo mais leve",
            reason=f"Detectado {len(slow_exps)} execucao(oes) lenta(s) nos registros",
            evidence=evidence,
            severity="suggestion",
        ))

    # Regra 3: Modelos que funcionaram bem
    ok_exps = [e for e in relevant_exps if e.result == "ok"]
    by_model: dict[str, list[ModelExp]] = defaultdict(list)
    for e in ok_exps:
        model_name = e.title.split(" — ")[0].strip() if " — " in e.title else e.title
        by_model[model_name].append(e)

    for model_name, model_exps in by_model.items():
        best = max(model_exps, key=lambda e: e.context_tokens)
        evidence = [f"{e.title} [{e.timestamp}]" for e in model_exps[:3]]
        recs.append(Recommendation(
            action=f"Modelo '{model_name}' funcionou com ate {best.context_tokens} tokens nesta configuracao",
            reason=f"{len(model_exps)} execucao(oes) bem-sucedida(s) registrada(s)",
            evidence=evidence,
            severity="info",
        ))

    # Regra 4: Muitas falhas
    if len(relevant_failures) >= 3:
        recent = relevant_failures[-3:]
        evidence = [f"{f.title} [{f.timestamp}]" for f in recent]
        recs.append(Recommendation(
            action="Revise as falhas recentes em 'bauer memory show failures'",
            reason=(
                f"{len(relevant_failures)} falha(s) registrada(s) — "
                "pode indicar problema sistematico"
            ),
            evidence=evidence,
            severity="warning",
        ))

    if not recs:
        recs.append(Recommendation(
            action="Nenhuma recomendacao disponivel no momento",
            reason=(
                "Poucos dados de aprendizado. "
                "Use 'bauer memory add-model-exp' para registrar experiencias."
            ),
            evidence=[],
            severity="info",
        ))

    return recs


class LearningEngine:
    """Motor de aprendizado baseado em regras (Fase 7).

    Le MODEL_EXPERIENCE.md e FAILED_ATTEMPTS.md.
    Gera recomendacoes auditaveis com motivo e evidencia.
    Nunca altera config. Nunca executa nada automaticamente.
    """

    def __init__(self, memory_dir: str | Path = "memory"):
        self.mm = MemoryManager(memory_dir)

    def load_experience(self) -> list[ModelExp]:
        text = self.mm.read_file("MODEL_EXPERIENCE.md")
        return parse_model_experience(text)

    def load_failures(self) -> list[FailedAttempt]:
        text = self.mm.read_file("FAILED_ATTEMPTS.md")
        return parse_failed_attempts(text)

    def recommend(self, machine_id: str = "") -> list[Recommendation]:
        """Gera recomendacoes com motivo e evidencia. Nunca altera config."""
        exps = self.load_experience()
        failures = self.load_failures()
        return generate_recommendations(exps, failures, machine_id)

    def summary(self) -> dict[str, int]:
        """Retorna contagem de entradas em cada arquivo de aprendizado."""
        return {
            "model_experiences": len(self.load_experience()),
            "failed_attempts": len(self.load_failures()),
        }

    def forget_model(self, model_name: str) -> dict[str, int]:
        """Remove todas as entradas de um modelo específico dos arquivos de aprendizado.

        Cria backup .bak antes de modificar. Retorna contagem de entradas removidas por arquivo.
        """
        import re

        results: dict[str, int] = {}
        files = {
            "MODEL_EXPERIENCE.md": model_name,
            "FAILED_ATTEMPTS.md": model_name,
        }

        for filename, target in files.items():
            p = self.mm.memory_dir / filename
            if not p.exists():
                continue

            content = p.read_text(encoding="utf-8")
            bak = p.with_suffix(".md.bak")
            bak.write_text(content, encoding="utf-8")

            # Divide em header + entradas individuais
            parts = content.split("\n---\n", 1)
            header = parts[0] + "\n---\n"
            body = parts[1] if len(parts) > 1 else ""

            # Separa cada entrada pelo padrão ## [timestamp]
            section_pattern = re.compile(r"(?=\n## \[)")
            sections = section_pattern.split(body)

            kept, removed = [], 0
            for section in sections:
                if target.lower() in section.lower():
                    removed += 1
                else:
                    kept.append(section)

            p.write_text(header + "".join(kept), encoding="utf-8")
            results[filename] = removed

        return results

    def reset(self) -> list[Path]:
        """Limpa arquivos de aprendizado, criando backup .bak antes.

        Nunca deleta. Preserva o cabecalho do arquivo.
        Retorna lista de arquivos resetados.
        """
        files_to_reset = [
            "FAILED_ATTEMPTS.md",
            "MODEL_EXPERIENCE.md",
            "RUNTIME_LESSONS.md",
        ]
        reset_paths: list[Path] = []
        for filename in files_to_reset:
            p = self.mm.memory_dir / filename
            if not p.exists():
                continue
            content = p.read_text(encoding="utf-8")
            bak = p.with_suffix(".md.bak")
            bak.write_text(content, encoding="utf-8")
            # Preserva apenas o cabecalho (ate o primeiro --- separador)
            parts = content.split("\n---\n", 1)
            header_only = parts[0] + "\n---\n"
            p.write_text(header_only, encoding="utf-8")
            reset_paths.append(p)
        return reset_paths


# ─── Learning Engine v2 — análise via LLM ────────────────────────────────────

_ANALYZE_PROMPT = """\
Você é um analista de sistemas de IA especializado em observabilidade e melhoria contínua.

Abaixo estão os arquivos de memória do Bauer Agent — um runtime local de LLMs.
Analise os dados e gere um relatório estruturado com insights acionáveis.

{memory_sections}

---

Gere um relatório com as seguintes seções (use exatamente esses títulos):

## Padrões Identificados
Liste os padrões recorrentes observados (sucessos, falhas, comportamentos).
Seja específico — cite modelos, contextos e erros concretos quando presentes.

## Recomendações de Modelo e Contexto
Com base nos dados, sugira o melhor modelo e tamanho de contexto para esta máquina.
Explique o motivo de cada sugestão.

## Lições Não Explícitas
Insights que estão implícitos nos dados mas não foram registrados explicitamente.
O que os padrões sugerem que ainda não foi documentado?

## Riscos Identificados
O que pode dar errado com a configuração atual? O que preocupa nos dados?

## Próximas Ações Sugeridas
Lista priorizada (máximo 5) de ações concretas para melhorar o sistema.
Formato: "1. [PRIORIDADE] Ação — Motivo"

Seja objetivo, técnico e acionável. Responda em português.
"""

_ANALYSIS_FILE = "LEARNING_ANALYSIS.md"
# Máximo de chars de cada arquivo de memória enviados ao LLM
_MAX_MEMORY_CHARS = 3000


@dataclass
class AnalysisResult:
    timestamp: str
    model_used: str
    report: str
    data_summary: dict[str, int]  # {arquivo: n_entradas}


class LearningEngineV2:
    """Motor de aprendizado v2 — usa LLM para análise em linguagem natural.

    Complementa o LearningEngine v1 (baseado em regras).
    Lê MODEL_EXPERIENCE, FAILED_ATTEMPTS e SKILLS_LEARNED,
    chama o modelo configurado e salva o relatório em LEARNING_ANALYSIS.md.

    Regras (igual v1):
    - Nunca altera config automaticamente.
    - Nunca executa nada — apenas analisa e sugere.
    - Resultado auditável e persistido em Markdown.
    """

    def __init__(self, memory_dir: str | Path = "memory"):
        self.mm = MemoryManager(memory_dir)
        self._v1 = LearningEngine(memory_dir)

    def _read_memory_section(self, filename: str, label: str) -> str:
        """Lê arquivo de memória e trunca para _MAX_MEMORY_CHARS."""
        content = self.mm.read_file(filename).strip()
        if not content or content.startswith("["):
            return f"### {label}\n(vazio)\n"
        truncated = content[:_MAX_MEMORY_CHARS]
        if len(content) > _MAX_MEMORY_CHARS:
            truncated += f"\n... (truncado — {len(content) - _MAX_MEMORY_CHARS} chars omitidos)"
        return f"### {label}\n```\n{truncated}\n```\n"

    def _build_memory_context(self) -> tuple[str, dict[str, int]]:
        """Monta o bloco de contexto com todos os arquivos de memória.

        Returns:
            (texto_para_llm, sumario_de_contagens)
        """
        v1_summary = self._v1.summary()
        sections = [
            self._read_memory_section("MODEL_EXPERIENCE.md", "Experiências com Modelos"),
            self._read_memory_section("FAILED_ATTEMPTS.md", "Tentativas Falhas"),
            self._read_memory_section("RUNTIME_LESSONS.md", "Lições de Runtime"),
            self._read_memory_section("SKILLS_LEARNED.md", "Skills Aprendidas"),
        ]
        return "\n".join(sections), v1_summary

    def analyze(self, model: str | None = None) -> AnalysisResult:
        """Chama o LLM para analisar os arquivos de memória.

        Args:
            model: modelo a usar (None = usa o configurado em config.yaml)

        Returns:
            AnalysisResult com o relatório gerado.

        Raises:
            RuntimeError se o modelo não responder.
        """
        from .config_loader import load_config
        from .env_loader import apply_env_to_config

        cfg = load_config()
        apply_env_to_config(cfg)
        model_name = model or cfg.model.name

        # Usa o provider configurado (não hardcoded Ollama)
        provider = cfg.model.provider
        if provider in ("openai", "openrouter", "custom", "groq", "mistral",
                        "xai", "together", "deepseek", "azure", "gemini",
                        "github", "copilot"):
            from .openai_client import OpenAIClient
            if provider == "openrouter":
                base_url = "https://openrouter.ai/api/v1"
                api_key = cfg.openrouter.api_key
            else:
                oa = getattr(cfg, "openai", None)
                base_url = oa.host if oa else "https://api.openai.com"
                api_key = oa.api_key if oa else ""
            client = OpenAIClient(base_url=base_url, api_key=api_key)
        else:
            from .ollama_client import OllamaClient
            client = OllamaClient(base_url=cfg.ollama.host)

        memory_context, data_summary = self._build_memory_context()
        prompt = _ANALYZE_PROMPT.format(memory_sections=memory_context)

        messages = [
            {
                "role": "system",
                "content": (
                    "Você é um analista técnico especializado em sistemas de IA. "
                    "Responda em português, de forma estruturada e acionável."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        chunks = list(client.chat_stream(model_name, messages))
        report = "".join(chunks).strip()

        if not report:
            raise RuntimeError(f"Modelo '{model_name}' retornou resposta vazia.")

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        result = AnalysisResult(
            timestamp=ts,
            model_used=model_name,
            report=report,
            data_summary=data_summary,
        )
        self._save_analysis(result)
        return result

    def _save_analysis(self, result: AnalysisResult) -> Path:
        """Persiste o relatório em LEARNING_ANALYSIS.md."""
        p = self.mm.memory_dir / _ANALYSIS_FILE
        header = (
            f"# LEARNING_ANALYSIS.md — Análise via LLM\n\n"
            f"Gerado automaticamente por `bauer learning analyze`.\n"
            f"Nunca editado manualmente. Não altera config.\n\n---\n\n"
        )

        entry = (
            f"## [{result.timestamp}] Análise — modelo: {result.model_used}\n\n"
            f"**Dados analisados:** "
            + ", ".join(f"{k}: {v}" for k, v in result.data_summary.items())
            + "\n\n"
            + result.report
            + "\n\n---\n\n"
        )

        if p.exists():
            existing = p.read_text(encoding="utf-8")
            # Insere nova análise após o header
            if "\n---\n\n" in existing:
                split_pos = existing.index("\n---\n\n") + len("\n---\n\n")
                new_content = existing[:split_pos] + entry + existing[split_pos:]
            else:
                new_content = existing + "\n" + entry
        else:
            new_content = header + entry

        p.write_text(new_content, encoding="utf-8")
        return p

    def load_last_analysis(self) -> str | None:
        """Retorna o texto da análise mais recente ou None se não existir."""
        p = self.mm.memory_dir / _ANALYSIS_FILE
        if not p.exists():
            return None
        content = p.read_text(encoding="utf-8")
        # Retorna a primeira entrada (mais recente) após o header
        parts = content.split("\n## [", 1)
        if len(parts) < 2:
            return None
        first_entry = "## [" + parts[1].split("\n## [")[0]
        return first_entry.strip()

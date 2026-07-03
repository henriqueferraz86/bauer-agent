"""App Factory tools: app_factory_init/status/score e verify_app.

Mixin herdado por ToolRouter. Ponte para os modulos ..app_factory (gates,
delivery score, containment) e ..app_verify (stack detection + smoke run).
verify_app persiste verify_result.json/verify_log.jsonl e limita tentativas.
"""

from __future__ import annotations

from pathlib import Path

from .base import ToolError


class FactoryToolsMixin:
    """Spec-Driven Development: inicializa, pontua e verifica apps gerados."""

    def _af_project_dir(self, args: dict):
        """Resolve a raiz do projeto-alvo: path explícito > projeto ativo > workspace."""
        sub = str(args.get("path", "") or "").strip()
        if sub and sub not in (".", "./"):
            return self._sandbox(sub)
        from .. import app_factory as af
        active = af.get_active_project(self.workspace)
        return active if active is not None else self.workspace

    def _app_factory_init(self, args: dict) -> str:
        from .. import app_factory as af

        idea = str(args.get("idea", "") or "").strip()
        if not idea:
            raise ToolError("app_factory_init: 'idea' é obrigatório.")
        stack = str(args.get("stack", "") or "").strip()
        overwrite = bool(args.get("overwrite", False))

        # 'path' é OBRIGATÓRIO: cada ideia vive na SUA pasta. Init na raiz do
        # workspace governava o workspace inteiro (compartilhado por todos os
        # projetos) e herdava/misturava estado de projetos anteriores.
        _path_err = (
            "app_factory_init: 'path' é obrigatório — informe a pasta do NOVO "
            "projeto usando o NOME DO APP em kebab-case (ex.: idea 'BauerInvest' "
            "→ path 'bauerinvest'). A raiz do workspace é compartilhada por "
            "vários projetos e NÃO pode ser governada. Nunca reutilize a pasta "
            "de outro projeto."
        )
        sub = str(args.get("path", "") or "").strip()
        if not sub or sub in (".", "./"):
            raise ToolError(_path_err)
        project_dir = self._sandbox(sub)
        try:
            if Path(project_dir).resolve() == Path(self.workspace).resolve():
                raise ToolError(_path_err)
        except OSError:
            pass

        # Anti-clobber: nunca sobrescrever um projeto existente/completo com
        # uma ideia nova só porque o modelo escolheu o mesmo nome de pasta.
        _ok, _why = af.guard_reinit(project_dir, idea=idea, overwrite=overwrite)
        if not _ok:
            return f"[app_factory_init] BLOQUEADO — {_why}"

        result = af.init_project(
            project_dir, idea=idea, stack=stack, overwrite=overwrite,
        )
        # Containment: fixa esta pasta como projeto ativo — TODO o código/arquivo
        # desta ideia fica contido aqui (nada solto na raiz nem em pastas irmãs).
        try:
            af.set_active_project(self.workspace, project_dir)
        except Exception:
            pass
        try:
            _proj_name = Path(project_dir).resolve().relative_to(
                Path(self.workspace).resolve()
            ).as_posix()
        except Exception:
            _proj_name = Path(project_dir).name
        _proj_name = _proj_name or "."
        written = result.get("written", [])
        lines = [
            f"[app_factory_init] Governança iniciada — gate: {result.get('gate')}.",
            f"  Ideia: {idea}",
            f"  Pasta do projeto (raiz ÚNICA): {_proj_name}/",
        ]
        if stack:
            lines.append(f"  Stack: {stack}")
        lines.append(f"  {len(written)} arquivo(s) criado(s) (docs/ + raiz).")
        lines.append(
            f"  CONTAINMENT: escreva TUDO sob '{_proj_name}/' (ex.: "
            f"'{_proj_name}/app/...', '{_proj_name}/frontend/...'). Arquivos fora "
            "dessa pasta serão bloqueados.\n"
            "  GATE DISCOVERY ativo — escrita de código BLOQUEADA.\n"
            "  SUA PRÓXIMA AÇÃO É UMA TOOL CALL — não responda em texto.\n"
            "  Chame 'clarify' quatro vezes, uma pergunta por vez:\n"
            "    clarify({\"question\": \"Quem são os usuários-alvo da aplicação?\"})\n"
            "    clarify({\"question\": \"Quais funcionalidades são essenciais na V1?\"})\n"
            "    clarify({\"question\": \"Há restrições de stack, prazo ou orçamento?\"})\n"
            "    clarify({\"question\": \"O que define sucesso para este projeto?\"})\n"
            "  Só após as 4 respostas, preencha os docs e avance o gate."
        )
        return "\n".join(lines)

    def _app_factory_status(self, args: dict) -> str:
        from .. import app_factory as af

        project_dir = self._af_project_dir(args)
        st = af.status(project_dir)
        if not st["governed"]:
            return (
                "[app_factory_status] Projeto NÃO está sob governança da App "
                "Factory. Use app_factory_init para iniciar."
            )
        lines = [
            f"[app_factory_status] gate: {st['gate']}",
            f"  planejamento completo: {'sim' if st['planning_complete'] else 'não'}",
        ]
        missing = st["missing_planning_docs"]
        if missing:
            lines.append(f"  docs pendentes: {', '.join(missing)}")
        sc = st.get("delivery_score") or {}
        if sc:
            lines.append(f"  delivery score parcial: {sc.get('score')}/10 "
                         f"({sc.get('satisfied')}/{sc.get('total')} itens)")
        return "\n".join(lines)

    def _app_factory_score(self, args: dict) -> str:
        from .. import app_factory as af

        project_dir = self._af_project_dir(args)
        if not af.is_governed(project_dir):
            return (
                "[app_factory_score] Projeto não governado — sem score. "
                "Use app_factory_init primeiro."
            )
        sc = af.delivery_score(project_dir)
        lines = [
            f"[app_factory_score] Delivery Score: {sc['score']}/10 "
            f"({'PRONTO' if sc['ready'] else 'NÃO pronto'} para V1)",
        ]
        for item, ok in sc["checks"].items():
            lines.append(f"  [{'x' if ok else ' '}] {item}")
        return "\n".join(lines)

    def _verify_app(self, args: dict) -> str:
        """P1.1 — auto-verificação: builda/roda/testa o app gerado e reporta."""
        from .. import app_verify as av
        from .. import app_factory as af

        # Resolve o projeto: path explícito > projeto ativo da App Factory > workspace.
        sub = str(args.get("path", "") or "").strip()
        if sub and sub not in (".", "./"):
            project_dir = self._sandbox(sub)
        else:
            active = af.get_active_project(self.workspace)
            project_dir = active if active is not None else self.workspace

        install = bool(args.get("install", True))
        try:
            timeout = int(args.get("timeout", 300))
        except (TypeError, ValueError):
            timeout = 300
        timeout = max(10, min(timeout, 1800))

        result = av.verify_project(project_dir, install=install, timeout=timeout)

        # P1.4: persiste resultado para o Delivery Score ler sem re-executar.
        _MAX_VERIFY_ATTEMPTS = 3
        _attempt_num = 1
        try:
            import json as _json
            import time as _time
            _meta = Path(project_dir) / ".bauer_meta"
            _meta.mkdir(parents=True, exist_ok=True)

            # Lê tentativas anteriores (se houver) para incrementar o contador.
            _prev_path = _meta / "verify_result.json"
            try:
                _prev = _json.loads(_prev_path.read_text(encoding="utf-8"))
                _attempt_num = int(_prev.get("attempts", 0)) + 1
            except Exception:
                _attempt_num = 1

            # smoke_passed: True apenas se o step "serve" (port probe) passou.
            # Distingue "build verde" de "app executado de verdade na porta".
            _smoke_ok = any(
                s.name == "serve" and s.ok and not s.skipped
                for s in result.steps
            )

            _result_data = {
                "ok": result.ok,
                "smoke_passed": _smoke_ok,
                "stack": result.stack,
                "summary": result.summary,
                "attempts": _attempt_num,
                "ts": _time.time(),
            }
            _prev_path.write_text(_json.dumps(_result_data), encoding="utf-8")

            # Apende ao log de tentativas para rastrear progresso de autocorrecao.
            _log_entry = {
                "attempt": _attempt_num,
                "ok": result.ok,
                "smoke_passed": _smoke_ok,
                "summary": result.summary,
                "ts": _time.time(),
                "steps": [
                    {"name": s.name, "ok": s.ok, "skipped": s.skipped,
                     "rc": s.rc, "output_tail": s.output[-500:] if s.output else ""}
                    for s in result.steps
                ],
            }
            _log_path = _meta / "verify_log.jsonl"
            with _log_path.open("a", encoding="utf-8") as _lf:
                _lf.write(_json.dumps(_log_entry) + "\n")
        except Exception:
            pass  # non-fatal

        lines = [f"[verify_app] {result.summary}", f"  projeto: {result.project}"]
        for s in result.steps:
            mark = "pulado" if s.skipped else ("ok" if s.ok else f"FALHOU rc={s.rc}")
            lines.append(f"  → {s.name}: {mark} ({' '.join(s.cmd)})")
            if not s.ok and not s.skipped and s.output:
                tail = s.output.strip().splitlines()[-15:]
                lines.append("    --- saída (cauda) ---")
                lines.extend(f"    {ln}" for ln in tail)
            elif s.skipped and s.reason:
                lines.append(f"    motivo: {s.reason}")
        lines.append(f"  tentativa: {_attempt_num}/{_MAX_VERIFY_ATTEMPTS}")
        if not result.ok:
            if _attempt_num >= _MAX_VERIFY_ATTEMPTS:
                lines.append(
                    f"  LIMITE ATINGIDO: {_attempt_num} tentativas sem sucesso. "
                    "Documente o que foi tentado no PROGRESS.md e reporte ao usuário — "
                    "não tente mais autocorrecao nesta sessão."
                )
            else:
                remaining = _MAX_VERIFY_ATTEMPTS - _attempt_num
                lines.append(
                    f"  AÇÃO: corrija o erro acima e rode verify_app de novo "
                    f"({remaining} tentativa(s) restante(s))."
                )
        return "\n".join(lines)

"""Orquestrador de agents — planeja, executa e sintetiza tarefas complexas.

Fluxo:
  1. Planejamento: modelo leve (qwen3:0.6b) decompoe a tarefa em passos com
     grafo de dependencias (campo depends_on por passo).
  2. Execucao: passos sem dependencias entre si rodam em paralelo via
     ThreadPoolExecutor; cada onda espera a anterior terminar.
  3. Persistencia: cada passo concluido e salvo em disco — use --resume
     para retomar uma execucao interrompida.
  4. Sintese: modelo combina resultados parciais em resposta coesa final.

Uso via CLI:
  bauer orchestrate run "sua tarefa complexa"
  bauer orchestrate run "tarefa longa" --resume
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from .agent import _build_system_prompt, run_one_turn
from .context_manager import ContextManager
from .model_router import ModelRouter
from .ollama_client import OllamaClient
from .unicode_utils import safe_json_dumps as _safe_json_dumps
from .tool_router import ToolRouter

MAX_STEPS = 6
STEP_CONTEXT = 8192

_PLANNER_PROMPT = """\
Voce e um orquestrador de tarefas. Decomponha a tarefa do usuario em passos sequenciais e atomicos.

Cada passo deve ser UMA acao que UM modelo de linguagem consegue executar de uma vez.
{agents_section}
REGRAS:
- Maximo de {max_steps} passos
- Cada passo tem:
    'goal'       instrucao clara em portugues
    'tools'      true se precisar de ferramentas do filesystem/shell/web
    'depends_on' lista de IDs de passos que devem terminar antes deste comecar
    'agent'      nome do agent especializado (da lista acima) ou "" para agent padrao
- Use depends_on: [] para passos que podem comecar imediatamente
- Passos com o mesmo conjunto de dependencias podem rodar em paralelo
- Responda APENAS com o JSON abaixo, nada mais
- NUNCA invente dados — se precisar de arquivos/exemplos, marque tools: true

Formato:
{{
  "objective": "objetivo principal em portugues",
  "steps": [
    {{"id": 1, "goal": "buscar dados necessarios",   "tools": true,  "depends_on": [], "agent": ""}},
    {{"id": 2, "goal": "analisar os dados",          "tools": false, "depends_on": [1], "agent": "python"}},
    {{"id": 3, "goal": "gerar relatorio em arquivo", "tools": true,  "depends_on": [1], "agent": "docs"}},
    {{"id": 4, "goal": "resumo final combinado",     "tools": false, "depends_on": [2, 3], "agent": ""}}
  ]
}}

Regras de depends_on:
  [] significa sem dependencia (pode executar imediatamente)
  [1] significa "execute depois que o passo 1 terminar"
  [2, 3] significa "execute depois que os passos 2 E 3 terminarem"

Regras de agent:
  "" usa o agent padrao (generalista)
  Use o nome exato de um agent da lista acima quando aquele especialista e o mais adequado para o passo

Exemplos de tarefas simples (sem paralelismo):
  Tarefa: "crie um script python fatorial"
  → 1 ou 2 passos lineares, todos com depends_on do passo anterior

Exemplos de tarefas paralelizaveis:
  Tarefa: "pesquise sobre IA e escreva um script de automacao"
  → passo 1 pesquisa (web), passo 2 escreve script (code) — ambos independentes (depends_on: [])
  → passo 3 combina resultados (depends_on: [1, 2])

Tarefa do usuario:"""

_SYNTHESIZER_PROMPT = """\
Voce e um sintetizador de resultados. Abaixo esta o objetivo original e os resultados parciais de cada passo executado.

Combine tudo em uma resposta coesa, clara e completa. Responda em portugues, em texto normal.

Objetivo original: {objective}

Resultados parciais:
{step_results}

Sua resposta final:"""


@dataclass
class OrchestratorConfig:
    planner_model: str = "qwen3:0.6b"
    synthesizer_model: str = "phi4-mini"
    max_steps: int = MAX_STEPS
    parallel_steps: bool = False  # False = sequencial (seguro para CPU/baixa RAM)
                                  # True  = paralelo (requer GPU ou RAM suficiente)
    max_retries: int = 2          # retentativas por passo em caso de falha (0 = sem retry)
    retry_delay_s: float = 3.0   # segundos entre tentativas
    agents_file: str = "agents.yaml"  # registry de agents para o planejador


@dataclass
class StepResult:
    id: int
    goal: str
    model_used: str
    response: str
    tool_log: list[dict] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class AgentOrchestrator:
    """Orquestrador: planeja -> executa (DAG + paralelo) -> sintetiza.

    Features:
    - Grafo de dependencias: passos declaram depends_on=[ids]
    - Execucao paralela: passos independentes rodam em threads simultaneas
    - Persistencia: progresso salvo em disco; --resume retoma execucao interrompida
    """

    def __init__(
        self,
        client: OllamaClient,
        tool_router: ToolRouter,
        model_router: ModelRouter,
        config: OrchestratorConfig | None = None,
        planner_client: OllamaClient | None = None,
        console: Console | None = None,
    ):
        self.client = client
        self.router = tool_router
        self.model_router = model_router
        self.config = config or OrchestratorConfig()
        # Client separado para planejamento/roteamento (sempre Ollama)
        self._planner_client = planner_client or client
        # Console Rich para streaming de saida em tempo real
        self.console = console

    # ------------------------------------------------------------------
    # internos — chamadas de modelo
    # ------------------------------------------------------------------

    def _call_model(self, model: str, messages: list[dict], stream_prefix: str = "") -> str:
        parts = []
        for chunk in self.client.chat_stream(model, messages):
            parts.append(chunk)
            if self.console and stream_prefix:
                self.console.print(chunk, end="", highlight=False)
        if self.console and stream_prefix:
            self.console.print()
        return "".join(parts)

    def _call_ollama(self, model: str, messages: list[dict], stream_prefix: str = "") -> str:
        parts = []
        for chunk in self._planner_client.chat_stream(model, messages):
            parts.append(chunk)
            if self.console and stream_prefix:
                self.console.print(chunk, end="", highlight=False)
        if self.console and stream_prefix:
            self.console.print()
        return "".join(parts)

    def _extract_json(self, text: str) -> dict | None:
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass
        block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if block:
            try:
                return json.loads(block.group(1))
            except json.JSONDecodeError:
                pass
        brace = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if brace:
            try:
                return json.loads(brace.group())
            except json.JSONDecodeError:
                pass
        return None

    # ------------------------------------------------------------------
    # planejamento
    # ------------------------------------------------------------------

    def plan(
        self,
        user_input: str,
        agents: list | None = None,
        specs: list | None = None,
    ) -> list[dict]:
        """Decompoe a tarefa em passos com grafo de dependencias.

        Args:
            user_input: Descricao da tarefa.
            agents: Lista de AgentDef disponíveis para o planejador designar por passo.
            specs: Lista de Spec aprovados/implementados como contratos do projeto.
        """
        if agents:
            lines = [f"  - {a.name}: {a.description}" for a in agents]
            agents_section = (
                "\nAgents especializados disponíveis (use o campo 'agent' para designar):\n"
                + "\n".join(lines)
                + "\n"
            )
        else:
            agents_section = ""

        if specs:
            spec_lines = [s.to_context(compact=True) for s in specs]
            specs_block = (
                "\nContratos do projeto (specs aprovados — respeite ao planejar):\n"
                + "\n".join(spec_lines)
                + "\n"
            )
            agents_section = agents_section + specs_block

        prompt = _PLANNER_PROMPT.format(
            max_steps=self.config.max_steps,
            agents_section=agents_section,
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_input},
        ]
        reply = self._call_ollama(self.config.planner_model, messages)
        plan = self._extract_json(reply)
        if plan and "steps" in plan and isinstance(plan["steps"], list):
            steps = plan["steps"][: self.config.max_steps]
            # Garante que campos opcionais existem em todos os passos
            for s in steps:
                s.setdefault("depends_on", [])
                s.setdefault("agent", "")
            return steps
        return [{"id": 1, "goal": user_input, "tools": True, "depends_on": [], "agent": ""}]

    # ------------------------------------------------------------------
    # grafo de dependencias (DAG)
    # ------------------------------------------------------------------

    def _build_dag(self, steps: list[dict]) -> dict[int, list[int]]:
        """Retorna {step_id: [dep_ids]}."""
        return {s["id"]: s.get("depends_on", []) for s in steps}

    def _topological_batches(self, steps: list[dict]) -> list[list[dict]]:
        """Agrupa passos em ondas — cada onda pode rodar em paralelo.

        Exemplo para steps 1 → {2, 3} → 4:
          Onda 0: [passo 1]
          Onda 1: [passo 2, passo 3]   ← paralelos
          Onda 2: [passo 4]

        Fallback sequencial se detectar dependencia circular.
        """
        dag = self._build_dag(steps)
        completed: set[int] = set()
        batches: list[list[dict]] = []
        remaining = list(steps)

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

    # ------------------------------------------------------------------
    # execucao de um passo
    # ------------------------------------------------------------------

    def _load_agent_system(self, agent_name: str) -> str:
        """Carrega o system prompt de um agent pelo nome. Retorna "" se nao encontrado."""
        if not agent_name:
            return ""
        try:
            from .agent_registry import AgentRegistry
            ag_reg = AgentRegistry(self.config.agents_file)
            ag = ag_reg.get(agent_name)
            return ag.system if ag else ""
        except Exception:
            return ""

    def execute_step(
        self,
        step: dict,
        previous_results: list[StepResult],
    ) -> StepResult:
        """Executa um unico passo com o melhor modelo disponivel.

        Se o passo tiver um campo 'agent', carrega o system prompt especializado
        daquele agent. Faz streaming ao console se console foi configurado.
        """
        goal = step.get("goal", "executar tarefa")
        needs_tools = step.get("tools", True)
        agent_name = step.get("agent", "")
        step_id = step.get("id", 0)

        # Carrega system prompt do agent especializado (se designado)
        agent_system = self._load_agent_system(agent_name)

        model_name, _route = self.model_router.select_model(goal)

        context_lines = [f"Objetivo deste passo: {goal}"]
        if previous_results:
            context_lines.append("\nResultados de passos anteriores:")
            for pr in previous_results:
                context_lines.append(
                    f"  Passo {pr.id} ({pr.model_used}): {pr.response[:500]}"
                )
        context_text = "\n".join(context_lines)

        stream_prefix = f"[passo {step_id}]"

        # Execução usa self.client (provider principal: Groq, OpenAI, Ollama…).
        # self._planner_client é reservado exclusivamente para planejamento (qwen3:0.6b).
        if needs_tools:
            base_system = _build_system_prompt(self.router)
            if agent_system:
                system_prompt = base_system + f"\n\n# ESPECIALIZACAO DO AGENT '{agent_name}'\n{agent_system}"
            else:
                system_prompt = base_system
            ctx = ContextManager(applied_context=STEP_CONTEXT, system_prompt=system_prompt)
            ctx.add_user(goal + "\n\n" + context_text)
            response, tool_log = run_one_turn(ctx, self.router, self.client, model_name)
        else:
            if agent_system:
                system_prompt = agent_system
            else:
                system_prompt = "Voce e um assistente util. Responda em portugues, em texto normal."
            ctx = ContextManager(applied_context=STEP_CONTEXT, system_prompt=system_prompt)
            ctx.add_user(goal + "\n\n" + context_text)
            if self.console:
                self.console.print(f"[dim]{stream_prefix}[/dim] ", end="")
            response = self._call_model(model_name, ctx.get_payload(), stream_prefix=stream_prefix)
            tool_log = []

        return StepResult(
            id=step_id,
            goal=goal,
            model_used=model_name,
            response=response,
            tool_log=tool_log,
        )

    # ------------------------------------------------------------------
    # retry por passo
    # ------------------------------------------------------------------

    def _execute_step_with_retry(
        self,
        step: dict,
        previous_results: list[StepResult],
    ) -> StepResult:
        """Executa um passo com retry automatico em caso de falha.

        Tenta ate (max_retries + 1) vezes com retry_delay_s entre tentativas.
        Se todas as tentativas falharem, retorna um StepResult de erro
        para nao bloquear a onda inteira — a sintese menciona o problema.
        """
        last_exc: Exception | None = None
        max_attempts = self.config.max_retries + 1

        for attempt in range(max_attempts):
            try:
                return self.execute_step(step, previous_results)
            except Exception as exc:
                last_exc = exc
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_delay_s)
                # continua para a proxima tentativa

        # Todas as tentativas falharam
        goal = step.get("goal", "")
        error_msg = (
            f"[Passo {step.get('id', '?')} falhou apos {max_attempts} tentativa(s): {last_exc}]"
        )
        return StepResult(
            id=step.get("id", 0),
            goal=goal,
            model_used="(erro)",
            response=error_msg,
            tool_log=[],
        )

    # ------------------------------------------------------------------
    # execucao paralela
    # ------------------------------------------------------------------

    def execute_parallel_steps(
        self,
        batch: list[dict],
        previous_results: list[StepResult],
    ) -> list[StepResult]:
        """Executa uma onda de passos com retry automatico por passo.

        Se parallel_steps=True (GPU/alta RAM): roda em threads simultaneas.
        Se parallel_steps=False (CPU/baixa RAM): roda um por vez, liberando
        o modelo do Ollama entre cada passo (evita OOM por dois modelos
        carregados ao mesmo tempo).

        Thread-safety quando paralelo:
        - _execute_step_with_retry cria um ContextManager novo por chamada
        - previous_results e passado como copia imutavel para cada thread
        """
        if len(batch) == 1 or not self.config.parallel_steps:
            # Sequencial: aguarda cada passo terminar antes de iniciar o proximo
            results = []
            for step in batch:
                results.append(self._execute_step_with_retry(step, list(previous_results)))
            return results

        # Paralelo: todos os passos da onda rodam ao mesmo tempo
        snapshot = list(previous_results)
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = {
                executor.submit(self._execute_step_with_retry, step, snapshot): step
                for step in batch
            }
            results = [future.result() for future in as_completed(futures)]

        results.sort(key=lambda r: r.id)
        return results

    # ------------------------------------------------------------------
    # persistencia de progresso
    # ------------------------------------------------------------------

    def _progress_path(self, task: str) -> Path:
        """Diretorio de progresso baseado em hash da tarefa."""
        h = hashlib.md5(task.encode("utf-8")).hexdigest()[:10]
        return Path(".orchestrate_progress") / h

    def save_plan(self, task: str, steps: list[dict]) -> None:
        """Salva o plano em disco para permitir --resume."""
        p = self._progress_path(task)
        p.mkdir(parents=True, exist_ok=True)
        (p / "plan.json").write_text(
            _safe_json_dumps(steps, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Salva task.txt para permitir listagem legível
        (p / "task.txt").write_text(task, encoding="utf-8")

    def load_plan(self, task: str) -> list[dict] | None:
        """Carrega plano salvo. Retorna None se nao existir."""
        f = self._progress_path(task) / "plan.json"
        if not f.exists():
            return None
        return json.loads(f.read_text(encoding="utf-8"))

    def save_progress(self, task: str, results: list[StepResult]) -> None:
        """Persiste resultados de passos concluidos."""
        p = self._progress_path(task)
        p.mkdir(parents=True, exist_ok=True)
        for r in results:
            (p / f"step_{r.id}.json").write_text(
                _safe_json_dumps(
                    {
                        "id": r.id,
                        "goal": r.goal,
                        "model_used": r.model_used,
                        "response": r.response,
                        "tool_log": r.tool_log,
                        "timestamp": r.timestamp,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

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

    def clear_progress(self, task: str) -> None:
        """Remove diretorio de progresso apos conclusao bem-sucedida."""
        p = self._progress_path(task)
        if p.exists():
            shutil.rmtree(p)

    def has_saved_progress(self, task: str) -> bool:
        """Verifica se existe progresso salvo para esta tarefa."""
        return self._progress_path(task).exists()

    def list_saved_progress(self) -> list[dict]:
        """Lista todas as tarefas com progresso salvo em disco.

        Returns:
            Lista de dicts com: hash, task, steps_done, steps_total, created.
        """
        base = Path(".orchestrate_progress")
        if not base.exists():
            return []

        entries = []
        for d in sorted(base.iterdir()):
            if not d.is_dir():
                continue
            # Lê nome da tarefa
            task_file = d / "task.txt"
            task_name = task_file.read_text(encoding="utf-8").strip() if task_file.exists() else d.name

            # Conta passos
            plan_file = d / "plan.json"
            steps_total = 0
            if plan_file.exists():
                try:
                    plan = json.loads(plan_file.read_text(encoding="utf-8"))
                    steps_total = len(plan)
                except Exception:
                    pass

            steps_done = len(list(d.glob("step_*.json")))

            import os
            created = ""
            try:
                from datetime import datetime
                ts = os.path.getctime(str(d))
                created = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass

            entries.append({
                "hash": d.name,
                "task": task_name,
                "steps_done": steps_done,
                "steps_total": steps_total,
                "created": created,
            })
        return entries

    # ------------------------------------------------------------------
    # sintese
    # ------------------------------------------------------------------

    def synthesize(self, objective: str, results: list[StepResult]) -> str:
        """Combina resultados parciais em resposta final coesa.

        Usa self.client (provider principal) para síntese.
        self._planner_client (OllamaClient) fica reservado só para planejamento.
        """
        steps_text = []
        for r in sorted(results, key=lambda x: x.id):
            tools_summary = ""
            if r.tool_log:
                names = list(dict.fromkeys(t["tool"] for t in r.tool_log))
                tools_summary = f" [tools: {', '.join(names)}]"
            steps_text.append(
                f"--- Passo {r.id} (modelo: {r.model_used}{tools_summary}) ---\n"
                f"Objetivo: {r.goal}\n"
                f"Resultado: {r.response[:1000]}"
            )

        messages = [
            {
                "role": "system",
                "content": _SYNTHESIZER_PROMPT.format(
                    objective=objective,
                    step_results="\n\n".join(steps_text),
                ),
            },
            {"role": "user", "content": "Sintetize os resultados."},
        ]

        # Usa self.client para síntese (provider principal: Groq, OpenAI, Ollama…).
        # - OllamaClient → usa synthesizer_model (phi4-mini, smollm3…)
        # - Cloud client → usa o modelo configurado no client (ex: llama-3.3-70b-versatile)
        from .ollama_client import OllamaClient as _OC
        if isinstance(self.client, _OC):
            return self._call_ollama(self.config.synthesizer_model, messages)
        else:
            cloud_model = getattr(self.client, "model", None) or self.config.synthesizer_model
            return self._call_model(cloud_model, messages)

    # ------------------------------------------------------------------
    # fluxo completo (API programatica)
    # ------------------------------------------------------------------

    def run(
        self,
        user_input: str,
        resume: bool = False,
    ) -> tuple[str, list[StepResult]]:
        """Executa o fluxo completo: plano → DAG → paralelo → sintese.

        Args:
            user_input: Descricao da tarefa.
            resume: Se True, retoma execucao salva em disco.

        Returns:
            (resposta_final, lista_de_resultados)
        """
        # Carrega plano salvo ou gera novo
        steps: list[dict] | None = self.load_plan(user_input) if resume else None
        if not steps:
            steps = self.plan(user_input)
            self.save_plan(user_input, steps)

        # Carrega resultados de passos ja concluidos
        done: dict[int, StepResult] = {
            r.id: r for r in (self.load_progress(user_input) if resume else [])
        }
        all_results: list[StepResult] = list(done.values())

        # Executa ondas (cada onda = passos independentes entre si)
        for batch in self._topological_batches(steps):
            pending = [s for s in batch if s["id"] not in done]
            if not pending:
                continue  # todos ja concluidos nesta onda

            batch_results = self.execute_parallel_steps(pending, all_results)
            all_results.extend(batch_results)
            for r in batch_results:
                done[r.id] = r
            self.save_progress(user_input, batch_results)

        if not steps:
            return "Nao foi possivel planejar os passos.", []

        objective = steps[0].get("goal", user_input)
        final = self.synthesize(objective, all_results)
        self.clear_progress(user_input)
        return final, all_results

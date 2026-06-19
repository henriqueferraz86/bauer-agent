"""Swarm consensus — múltiplos agentes respondem, reducer escolhe o melhor.

Evolução do mixture_of_agents (tool_router.py): adiciona estratégias de consensus
(majority vote, best-of-n, synthesis) e pontuação estruturada por resposta.

Uso direto (sem tool router):
    swarm = SwarmRunner(clients=[client1, client2, client3])
    result = swarm.run("Explique recursão", strategy="best_of_n")
    print(result.winner)
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ConsensusStrategy(str, Enum):
    BEST_OF_N = "best_of_n"
    MAJORITY = "majority"
    SYNTHESIS = "synthesis"


@dataclass
class AgentVote:
    """Resposta de um agente individual."""
    agent_id: str
    model: str
    response: str
    elapsed_s: float
    score: float = 0.0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.response.strip())


@dataclass
class SwarmResult:
    """Resultado consolidado do swarm."""
    winner: str
    strategy: str
    votes: List[AgentVote]
    elapsed_s: float
    n_ok: int
    n_failed: int
    metadata: Dict[str, Any] = field(default_factory=dict)


def _score_response(response: str, query: str) -> float:
    """Pontua uma resposta heuristically (sem LLM).

    Critérios: comprimento normalizado + presença de palavras da query +
    estrutura (listas, code fences).
    """
    if not response.strip():
        return 0.0

    score = 0.0
    words = len(response.split())
    # Comprimento: bom entre 50–500 palavras
    if words < 10:
        score += 0.1
    elif words < 50:
        score += 0.3
    elif words <= 500:
        score += 0.5 + min(words / 1000, 0.2)
    else:
        score += 0.4  # muito longa penaliza levemente

    # Palavras da query presentes
    query_words = {w.lower() for w in query.split() if len(w) > 3}
    resp_lower = response.lower()
    if query_words:
        overlap = sum(1 for w in query_words if w in resp_lower)
        score += 0.2 * (overlap / len(query_words))

    # Estrutura: marcadores de lista, code fences
    if "```" in response:
        score += 0.1
    if any(line.startswith(("- ", "* ", "1.", "•")) for line in response.splitlines()):
        score += 0.05

    return min(score, 1.0)


class SwarmRunner:
    """Orquestra N agentes LLM em paralelo e aplica estratégia de consensus."""

    def __init__(
        self,
        clients: List[Any],
        models: Optional[List[str]] = None,
        max_workers: int = 4,
        timeout_s: float = 60.0,
        scorer: Optional[Callable[[str, str], float]] = None,
    ) -> None:
        self._clients = clients
        self._models = models or [None] * len(clients)
        self._max_workers = max_workers
        self._timeout_s = timeout_s
        self._scorer = scorer or _score_response

        if len(self._models) < len(self._clients):
            self._models += [None] * (len(self._clients) - len(self._models))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        query: str,
        strategy: ConsensusStrategy | str = ConsensusStrategy.BEST_OF_N,
        system_prompt: str = "",
        synthesis_client: Optional[Any] = None,
        synthesis_model: Optional[str] = None,
    ) -> SwarmResult:
        """Executa o swarm e aplica a estratégia de consensus."""
        strategy = ConsensusStrategy(strategy)
        t0 = time.time()

        votes = self._gather_votes(query, system_prompt)
        ok_votes = [v for v in votes if v.ok]
        failed = len(votes) - len(ok_votes)

        if not ok_votes:
            return SwarmResult(
                winner="[swarm] Nenhuma resposta disponível.",
                strategy=strategy.value,
                votes=votes,
                elapsed_s=round(time.time() - t0, 2),
                n_ok=0,
                n_failed=failed,
            )

        if strategy == ConsensusStrategy.BEST_OF_N:
            winner_text, meta = self._best_of_n(ok_votes, query)
        elif strategy == ConsensusStrategy.MAJORITY:
            winner_text, meta = self._majority(ok_votes)
        elif strategy == ConsensusStrategy.SYNTHESIS:
            winner_text, meta = self._synthesis(
                ok_votes, query, synthesis_client, synthesis_model
            )
        else:
            winner_text, meta = ok_votes[0].response, {}

        return SwarmResult(
            winner=winner_text,
            strategy=strategy.value,
            votes=votes,
            elapsed_s=round(time.time() - t0, 2),
            n_ok=len(ok_votes),
            n_failed=failed,
            metadata=meta,
        )

    # ------------------------------------------------------------------
    # Vote gathering
    # ------------------------------------------------------------------

    def _gather_votes(self, query: str, system_prompt: str) -> List[AgentVote]:
        agents = list(enumerate(zip(self._clients, self._models)))
        votes: List[AgentVote] = []

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {
                pool.submit(self._call_agent, i, client, model, query, system_prompt): i
                for i, (client, model) in agents
            }
            for fut in as_completed(futures, timeout=self._timeout_s * 1.1):
                try:
                    votes.append(fut.result(timeout=1))
                except Exception as exc:
                    idx = futures[fut]
                    votes.append(AgentVote(
                        agent_id=f"agent_{idx}",
                        model=str(self._models[idx]),
                        response="",
                        elapsed_s=0.0,
                        error=str(exc),
                    ))

        return votes

    def _call_agent(
        self,
        idx: int,
        client: Any,
        model: Optional[str],
        query: str,
        system_prompt: str,
    ) -> AgentVote:
        t0 = time.time()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": query})

        try:
            if model:
                parts = list(client.chat_stream(model, messages))
            else:
                # Client pode ter modelo padrão
                model = getattr(client, "default_model", None) or "default"
                parts = list(client.chat_stream(model, messages))
            response = "".join(parts)
            elapsed = round(time.time() - t0, 2)
            score = self._scorer(response, query)
            return AgentVote(
                agent_id=f"agent_{idx}",
                model=str(model),
                response=response,
                elapsed_s=elapsed,
                score=score,
            )
        except Exception as exc:
            return AgentVote(
                agent_id=f"agent_{idx}",
                model=str(model),
                response="",
                elapsed_s=round(time.time() - t0, 2),
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Estratégias de consensus
    # ------------------------------------------------------------------

    def _best_of_n(
        self, votes: List[AgentVote], query: str
    ) -> tuple[str, Dict[str, Any]]:
        """Escolhe a resposta com maior score heurístico."""
        for v in votes:
            if v.score == 0.0:
                v.score = self._scorer(v.response, query)
        best = max(votes, key=lambda v: v.score)
        meta = {
            "winner_agent": best.agent_id,
            "winner_score": round(best.score, 3),
            "scores": {v.agent_id: round(v.score, 3) for v in votes},
        }
        return best.response, meta

    def _majority(
        self, votes: List[AgentVote]
    ) -> tuple[str, Dict[str, Any]]:
        """Voto por maioria: agrupa respostas similares (by first 40 chars), pega maior grupo."""
        from collections import Counter

        buckets: Dict[str, List[AgentVote]] = {}
        for v in votes:
            key = v.response.strip()[:40].lower()
            buckets.setdefault(key, []).append(v)

        # Maior grupo
        largest_key = max(buckets, key=lambda k: len(buckets[k]))
        group = buckets[largest_key]
        # Dentro do grupo, melhor score
        winner = max(group, key=lambda v: v.score)
        meta = {
            "majority_size": len(group),
            "total": len(votes),
            "winner_agent": winner.agent_id,
        }
        return winner.response, meta

    def _synthesis(
        self,
        votes: List[AgentVote],
        query: str,
        synth_client: Optional[Any],
        synth_model: Optional[str],
    ) -> tuple[str, Dict[str, Any]]:
        """Usa um modelo sintetizador para combinar as respostas."""
        if synth_client is None:
            # Fallback: best_of_n
            return self._best_of_n(votes, query)

        responses_txt = "\n\n".join(
            f"[Agente {v.agent_id}]:\n{v.response}" for v in votes
        )
        synthesis_prompt = (
            f"Você recebeu as seguintes respostas de múltiplos agentes para a pergunta:\n"
            f'"{query}"\n\n'
            f"Respostas:\n{responses_txt}\n\n"
            f"Sintetize a melhor resposta combinando os pontos mais relevantes de cada agente. "
            f"Seja conciso e preciso."
        )
        messages = [{"role": "user", "content": synthesis_prompt}]
        try:
            model = synth_model or getattr(synth_client, "default_model", "default")
            parts = list(synth_client.chat_stream(model, messages))
            result = "".join(parts)
            return result, {"strategy_detail": "synthesis_ok", "n_inputs": len(votes)}
        except Exception as exc:
            logger.warning("swarm: synthesis fallback to best_of_n: %s", exc)
            return self._best_of_n(votes, query)

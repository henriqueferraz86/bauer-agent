"""Roteador inteligente entre modelos.

Classifica a pergunta do usuário e redireciona para o modelo mais adequado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

RouteKind = Literal["direct", "code", "reasoning", "tool", "orchestrate"]


@dataclass
class Route:
    kind: RouteKind
    label: str
    model: str


@dataclass
class RouterConfig:
    enabled: bool = True
    router_model: str = "qwen3:0.6b"
    default_model: str = "phi4-mini"
    routes: list[Route] = field(default_factory=lambda: [
        Route("code", "codigo", "Impulse2000/smollm3:latest"),
        Route("reasoning", "raciocinio", "phi4-mini:latest"),
        Route("tool", "ferramenta", "phi4-mini:latest"),
        Route("direct", "direto", "qwen3:0.6b"),
        Route("orchestrate", "orquestrar", "phi4-mini:latest"),
    ])

    def route_for(self, kind: RouteKind) -> Route:
        for r in self.routes:
            if r.kind == kind:
                return r
        return Route("direct", "direto", self.router_model)


_CLASSIFY_PROMPT = (
    "Classifique a mensagem do usuario em UMA das categorias abaixo.\n"
    "Responda APENAS com a palavra da categoria, nada mais.\n\n"
    "Categorias:\n"
    "  direct      — saudacao, conversa simples, perguntas faceis (horas, data, clima)\n"
    "  code        — pedido de codigo, script, debugging, programacao\n"
    "  reasoning   — explicacao complexa, matematica, logica, analise profunda\n"
    "  tool        — ler/escrever/listar arquivos, shell, web search\n"
    "  orchestrate — tarefa complexa com MULTIPLOS objetivos distintos que exige\n"
    "                pesquisa + codigo + analise + arquivos ao mesmo tempo\n\n"
    "Regra para orchestrate: use SOMENTE quando a tarefa tiver 3 ou mais objetivos\n"
    "claramente diferentes que nao podem ser resolvidos em uma unica resposta.\n\n"
    "Exemplos:\n"
    "  'oi' -> direct\n"
    "  'crie um script python' -> code\n"
    "  'explique relatividade' -> reasoning\n"
    "  'liste os arquivos' -> tool\n"
    "  'pesquise sobre IA' -> tool\n"
    "  'pesquise sobre Python, analise o projeto atual e gere um relatorio completo' -> orchestrate\n"
    "  'crie um sistema de monitoramento: busque logs, analise erros e gere dashboard' -> orchestrate\n"
    "  'crie um script' -> code\n"
    "  'pesquise e salve o resultado' -> tool\n\n"
    "Mensagem:"
)


class ModelRouter:
    """Classifica e roteia mensagens para o melhor modelo disponível.

    Categorias:
      direct      — modelo leve (qwen3:0.6b)
      code        — modelo de código (smollm3)
      reasoning   — modelo de raciocínio (phi4-mini)
      tool        — modelo com tools (phi4-mini)
      orchestrate — escala para AgentOrchestrator (múltiplos passos)
    """

    def __init__(self, client, config: RouterConfig | None = None):
        self.client = client
        self.config = config or RouterConfig()

    def classify(self, user_input: str) -> RouteKind:
        """Usa o modelo roteador para classificar a mensagem."""
        messages = [
            {"role": "system", "content": _CLASSIFY_PROMPT},
            {"role": "user", "content": user_input},
        ]
        try:
            parts = []
            for chunk in self.client.chat_stream(self.config.router_model, messages):
                parts.append(chunk)
            reply = "".join(parts).strip().lower()
        except Exception:
            # Se o roteador falhar, usa o modelo padrão
            return "reasoning"

        if "orchestrate" in reply or "orquestra" in reply:
            return "orchestrate"
        if "code" in reply or "codigo" in reply:
            return "code"
        if "reasoning" in reply or "raciocinio" in reply:
            return "reasoning"
        if "tool" in reply or "ferramenta" in reply:
            return "tool"
        return "direct"

    def select_model(self, user_input: str) -> tuple[str, Route]:
        """Retorna (model_name, route) para a mensagem."""
        kind = self.classify(user_input)
        route = self.config.route_for(kind)
        return route.model, route

"""Roteador inteligente entre modelos.

Classifica a pergunta do usuário e redireciona para o modelo mais adequado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

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


# ─── Fase 12 / Sprint 34: roteamento por task_type/complexity + profiles ──────
#
# Evolução do router acima: em vez de gastar uma CHAMADA LLM só para classificar
# (o que adiciona latência — contraproducente para o objetivo de performance),
# um classificador HEURÍSTICO (sem rede) decide o tipo/complexidade da tarefa e
# aponta um "tier" de modelo (fast/balanced/coding/heavy). Conservador: na
# dúvida cai em `balanced`, e só escala para `heavy` com sinais claros de
# complexidade. Isto é a CAMADA DE DECISÃO — não troca o modelo do turno por si
# só; `bauer models route` a expõe para inspeção/ajuste antes de confiar nela.

TaskType = Literal["conversation", "tool_call", "coding", "reasoning", "architecture"]
Complexity = Literal["low", "medium", "high"]

#: tier recomendado por (task_type, complexity). Tiers: fast|balanced|coding|heavy.
_TIER: dict[tuple[str, str], str] = {
    ("conversation", "low"): "fast",
    ("conversation", "medium"): "fast",
    ("conversation", "high"): "balanced",
    ("tool_call", "low"): "fast",
    ("tool_call", "medium"): "balanced",
    ("tool_call", "high"): "balanced",
    ("reasoning", "low"): "balanced",
    ("reasoning", "medium"): "balanced",
    ("reasoning", "high"): "heavy",
    ("coding", "low"): "coding",
    ("coding", "medium"): "coding",
    ("coding", "high"): "heavy",
    ("architecture", "low"): "heavy",
    ("architecture", "medium"): "heavy",
    ("architecture", "high"): "heavy",
}

# Sinais de CÓDIGO/build. `api` NÃO entra (casa "FastAPI"/"rápida" e engolia
# buscas); use "endpoint"/"rest api" para intenção de API.
_CODING = ("código", "codigo", "code", "script", "função", "funcao", "function",
           "bug", "debug", "refator", "refactor", "classe", "endpoint", "rest api",
           "implementa", "implement", "compil", "stacktrace", "traceback",
           "teste unit", "unit test", "typescript", "python", "javascript",
           # build/frontend
           "site", "frontend", "front-end", "webapp", "aplicativo", "componente",
           "landing page", "página web", "pagina web", "react", "vue", "html", "css")
_ARCH = ("arquitetur", "architecture", "redesenh", "redesign", "múltiplos backends",
         "multiplos backends", "migra", "migrat", "sistema inteiro", "reescrev",
         "rewrite", "projete o", "design a system", "escalabilidade", "trade-off",
         "tradeoff", "compare abordagens")
_TOOL = ("liste", "list", "leia", "read", "rode", "run ", "execute", "pesquis",
         "search", "abra", "mostre", "show", "busque", "fetch", "baixe", "download",
         # arquivos / infra / kanban — operações que rodam tools, não raciocínio
         "arquivo", "pasta", "diretório", "diretorio", "docker", "compose",
         "container", "logs", "kanban", "tarefa no kanban", "no kanban")
_CONVERSATION = ("oi", "olá", "ola", "hi", "hello", "hey", "bom dia", "boa tarde",
                 "boa noite", "obrigado", "obrigada", "valeu", "thanks", "quem é você",
                 "quem e voce", "o que você faz", "o que voce faz", "tudo bem")


@dataclass
class ModelProfile:
    name: str            # fast | balanced | coding | heavy
    provider: str = ""
    model: str = ""


@dataclass
class RouteDecision:
    task_type: str
    complexity: str
    profile: str                 # tier: fast|balanced|coding|heavy
    reason: str = ""
    matched: list[str] = field(default_factory=list)  # sinais que dispararam
    provider: str = ""           # resolvido se profiles configurados
    model: str = ""


def _has(text: str, needles: tuple[str, ...]) -> list[str]:
    return [n for n in needles if n in text]


def classify_task(message: str) -> RouteDecision:
    """Classifica a mensagem em (task_type, complexity) → tier, SEM chamar LLM.

    Conservador: sinal de código/arquitetura escala; ausência de sinal cai em
    `reasoning`/`balanced` (nunca no tier fraco por engano)."""
    text = (message or "").strip().lower()
    words = len(text.split())
    matched: list[str] = []

    arch = _has(text, _ARCH)
    coding = _has(text, _CODING)
    tool = _has(text, _TOOL)
    conv = _has(text, _CONVERSATION)
    has_code_block = "```" in message

    # Complexidade: tamanho + múltiplos objetivos como sinais.
    multi = any(s in text for s in (" e depois", "; ", " e também", " e tambem", "vários", "varios"))
    if words <= 12 and not (arch or has_code_block):
        complexity = "low"
    elif words >= 40 or arch or multi:
        complexity = "high"
    else:
        complexity = "medium"

    # Tipo de tarefa (ordem = prioridade). Arquitetura > código > tool > conversa.
    if arch:
        task, matched = "architecture", arch
    elif coding or has_code_block:
        task = "coding"
        matched = coding + (["```code-block```"] if has_code_block else [])
    elif conv and words <= 12 and not tool:
        task, complexity, matched = "conversation", "low", conv
    elif tool:
        task, matched = "tool_call", tool
    else:
        task = "reasoning"

    tier = _TIER.get((task, complexity), "balanced")
    reason = (
        f"tarefa '{task}' de complexidade '{complexity}' → tier '{tier}'"
        + (f" (sinais: {', '.join(matched[:4])})" if matched else " (sem sinais fortes → caminho seguro)")
    )
    return RouteDecision(task_type=task, complexity=complexity, profile=tier, reason=reason, matched=matched)


def decide(message: str, profiles: "dict[str, ModelProfile] | None" = None) -> RouteDecision:
    """classify_task + resolve provider/model do profile (se configurado)."""
    d = classify_task(message)
    if profiles and d.profile in profiles:
        p = profiles[d.profile]
        d.provider, d.model = p.provider, p.model
    return d


def _spec_field(spec, key: str) -> str:
    if isinstance(spec, dict):
        return str(spec.get(key, "") or "")
    return str(getattr(spec, key, "") or "")


#: Providers que rodam NA PRÓPRIA MÁQUINA. `custom` fica de fora da lista fixa
#: porque depende do host: aponta para onde o usuário mandar, e pode ser uma API
#: externa — ver `provider_e_local`.
PROVIDERS_LOCAIS = frozenset({"ollama", "lmstudio"})

#: Hosts que caracterizam "esta máquina" para o provider `custom`.
_HOSTS_LOCAIS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal")


def provider_e_local(provider: str, cfg: Any = None) -> bool:
    """O provider roda nesta máquina?

    `ollama` e `lmstudio` sempre. `custom` só quando o host aponta para a
    máquina local — tratá-lo como local por padrão transformaria `--local` numa
    promessa que qualquer endpoint externo quebraria em silêncio.
    """
    p = (provider or "").strip().lower()
    if p in PROVIDERS_LOCAIS:
        return True
    if p != "custom" or cfg is None:
        return False
    endpoint = str(getattr(getattr(cfg, "custom", None), "host", "") or "").strip()
    try:
        hostname = (urlparse(endpoint).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    return hostname in _HOSTS_LOCAIS


def validar_execucao_local(cfg, model_name: str = "") -> "list[str]":
    """Problemas que impedem `--local` de cumprir o que promete. [] = pode subir.

    Fechar só o roteamento faria a flag MENTIR: existem três portas para a
    nuvem, e um usuário que digita `--local` está pedindo que nenhuma abra.

      1. `profiles_local`  — sem ele não há para onde rotear
      2. `model.name`      — caminhos sem tier vão direto nele
      3. `fallback_models` — se o local engasgar, é para onde o Bauer corre

    A 2 e a 3 são as traiçoeiras: com elas apontando para fora, um hiccup do
    Ollama manda o contexto para a nuvem sem uma linha na tela.

    NÃO verifica se o modelo funciona — só se é local. São problemas diferentes
    e misturá-los deixaria a mensagem confusa: o `bauer doctor` diz se o Ollama
    está no ar; este diz se algo sairia da máquina.
    """
    problemas: list[str] = []
    section = getattr(cfg, "model", None) or getattr(cfg, "models", None)

    if not (getattr(section, "profiles_local", None) or {}):
        problemas.append(
            "`model.profiles_local` ausente no config — sem ele não há tiers "
            "locais para rotear")

    alvo = (model_name or str(getattr(section, "name", "") or "")).strip()
    prov = str(getattr(section, "provider", "") or "")
    if not provider_e_local(prov, cfg):
        problemas.append(
            f"`model.name` = {alvo or '(vazio)'} usa provider '{prov}', que não "
            f"é local — caminhos sem roteamento por tier iriam para fora")

    # A lista que importa com `--local` e a LOCAL — `fallback_models` continua
    # valendo (e podendo apontar para a nuvem) no modo normal. Sem esse par, o
    # usuario teria de escolher entre ter `--local` e ter socorro grande no modo
    # de nuvem; com ele, nenhum modo perde nada.
    remotos = [f"{fb.provider}/{fb.name}"
               for fb in (getattr(section, "fallback_models_local", None) or [])
               if not provider_e_local(getattr(fb, "provider", ""), cfg)]
    if remotos:
        problemas.append(
            f"`model.fallback_models_local` tem {len(remotos)} destino(s) na nuvem "
            f"({', '.join(remotos[:3])}) — uma falha do modelo local cairia lá")
    return problemas


def profiles_by_provider_from_config(cfg) -> "dict[str, dict[str, ModelProfile]]":
    """Lê `model.profiles_by_provider` — tiers dedicados por PROVIDER ativo.

    Existe para o roteamento seguir o provider escolhido via `/model` ao vivo:
    trocar de openai para openrouter (ou o inverso) também troca qual
    conjunto de tiers vale, sem reiniciar a sessão. Provider sem entrada aqui
    não tem tiers dedicados — o caller cai no `profiles` plano de sempre em
    vez de arriscar rotear para um provider que o usuário não escolheu.

    Formato esperado (config.yaml):
        model:
          profiles_by_provider:
            openrouter:
              fast:     {provider: openrouter, model: deepseek/deepseek-v4-flash}
              balanced: {provider: openrouter, model: deepseek/deepseek-v3.2}
            openai:
              fast:     {provider: openai, model: gpt-5.6-mini}
              heavy:    {provider: openai, model: gpt-5.6}
    """
    out: dict[str, dict[str, ModelProfile]] = {}
    try:
        section = getattr(cfg, "model", None) or getattr(cfg, "models", None)
        raw = getattr(section, "profiles_by_provider", None) or {}
        if isinstance(raw, dict):
            for provider, tiers in raw.items():
                if not isinstance(tiers, dict):
                    continue
                resolved: dict[str, ModelProfile] = {}
                for name, spec in tiers.items():
                    resolved[name] = ModelProfile(name=name, provider=_spec_field(spec, "provider"),
                                                  model=_spec_field(spec, "model"))
                out[str(provider)] = resolved
    except Exception as exc:  # noqa: BLE001 — opcional; não quebra
        from .logging_config import log_suppressed
        log_suppressed("model_router.profiles_by_provider_from_config", exc)
    return out


def profiles_from_config(cfg, *, conjunto: str = "default") -> "dict[str, ModelProfile]":
    """Lê os tiers do config (best-effort). Vazio se ausente.

    ``conjunto="local"`` lê `model.profiles_local` em vez de `model.profiles` —
    é como o `--local` mantém o roteamento ligado e só troca o destino.

    Formato esperado (config.yaml):
        model:
          profiles:
            fast:     {provider: openrouter, model: deepseek/deepseek-v4-flash}
            balanced: {provider: openrouter, model: deepseek/deepseek-v3.2}
            coding:   {provider: openrouter, model: qwen/qwen3-coder-flash}
            heavy:    {provider: openrouter, model: deepseek/deepseek-r1}
          profiles_local:
            fast:     {provider: ollama, model: qwen2.5-coder:3b}
            coding:   {provider: ollama, model: qwen3-coder:30b}
    """
    campo = "profiles_local" if conjunto == "local" else "profiles"
    out: dict[str, ModelProfile] = {}
    try:
        # `model` (singular) é o campo real do schema; tolera `models` p/ callers de teste.
        section = getattr(cfg, "model", None) or getattr(cfg, "models", None)
        raw = getattr(section, campo, None) or {}
        if isinstance(raw, dict):
            for name, spec in raw.items():
                out[name] = ModelProfile(name=name, provider=_spec_field(spec, "provider"),
                                         model=_spec_field(spec, "model"))
    except Exception as exc:  # noqa: BLE001 — profiles são opcionais; não quebra
        from .logging_config import log_suppressed
        log_suppressed("model_router.profiles_from_config", exc)
    return out

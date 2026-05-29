# Bauer Agent

Runtime adaptativo para LLMs locais.

> Hermes é rígido. Bauer é adaptativo.
> Roda com o que tem, ajusta o que precisar, avisa claramente.

## Estado atual: Fase 1 — Núcleo confiável

Esta fase entrega apenas diagnóstico. Sem chat, sem tools, sem RAG, sem aprendizado.

```bash
bauer doctor              # diagnóstico completo
bauer config validate     # valida config.yaml
bauer models list         # lista modelos do models.yaml
bauer auth providers      # lista providers de autenticação
bauer auth login          # autentica com provider (OAuth ou API Key)
bauer auth status         # mostra providers autenticados
```

## Instalação rápida

```bash
pip install -e .
# ou
pip install typer rich pydantic pyyaml httpx psutil
```

## Documentos de referência

- `BauerAgent.md` — especificação técnica.
- `premortembauer.md` — riscos antecipados.
- `bauer-decisions.md` — decisões duras tomadas antes da Fase 1.
- `Guia_Todas_Fases_Claude_Codex_Bauer.md` — fases 1 a 8.
- `PassoAPasso_Claude_OpenAI_Bauer.md` — fluxo Claude + Codex.
- `bauer/auth.py` — módulo de autenticação via browser/API Key.

## Princípio do projeto

> Subir sem dor é mais importante que ter muitas features.

Ordem obrigatória: confiável → adaptativo → aprendiz → especializado.

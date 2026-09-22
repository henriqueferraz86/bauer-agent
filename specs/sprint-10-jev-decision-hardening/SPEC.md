# SPEC — Sprint 10: Decisão estruturada opcional com Jev

## Problema

O Bauer já possui roteamento heurístico, Kernel, governança e times, mas a
classificação de tarefa ainda depende de palavras-chave. Isso limita a escolha
de perfil, a indicação de orquestração e a tomada de decisão com confiança
explícita.

## Objetivo

Adicionar Jev como um decisor estruturado opcional, com ativação e desativação
por configuração, fallback automático para o roteador heurístico e integração
nos caminhos `agent`, `serve` e `run`.

## Escopo

- Seção estrita `decision` no `BauerConfig`.
- Cliente HTTP pequeno para a API System One, usando `httpx` já presente.
- Resultado tipado com origem, confiança, motivo e erro sanitizado.
- Fallback determinístico quando Jev estiver desligado, sem credencial, falhar
  ou retornar confiança abaixo do limite.
- Uso do Jev para selecionar tier; execução, tools, aprovação e Kernel continuam
  no Bauer.
- Correção dos erros de tipagem bloqueantes introduzidos no runtime atual.
- Testes unitários herméticos sem rede.

## Fora de escopo

- Tornar Jev obrigatório.
- Permitir que Jev execute comandos, aprove ações ou altere políticas.
- Alterar a memória automática ou migrar dados existentes.
- Reescrever todos os módulos grandes em uma única sprint.

## Configuração

```yaml
decision:
  jev_enabled: false
  fallback_enabled: true
  api_key: ""
  endpoint: "https://api.typesafe.ai/v1/systemone"
  model: "jev-latest"
  timeout_seconds: 2.0
  min_confidence: 0.65
```

`TYPESAFE_API_KEY` tem precedência sobre `decision.api_key`.

## Critérios de aceite

1. Configuração padrão mantém o comportamento atual e não faz chamadas de rede.
2. Jev desligado nunca importa nem chama o cliente HTTP.
3. Jev habilitado retorna uma decisão estruturada para os quatro tiers atuais.
4. Erro HTTP, timeout, JSON inválido, credencial ausente ou baixa confiança
   usam o fallback heurístico sem quebrar o turno.
5. A decisão registra `source=jev` ou `source=heuristic` e não expõe a chave.
6. `agent`, `serve` e `run` usam a mesma função de decisão.
7. Nenhuma decisão do Jev contorna Kernel, policy, approval, allowlist ou gates.
8. `uv run pytest tests/ -q --tb=short`, Ruff crítico e mypy passam.

## Skills obrigatórias

- spec-driven-project-setup
- security-review
- test-strategy

## Sub-agents recomendados

- backend-implementer
- test-engineer
- security-reviewer
- code-reviewer

# Jev no Bauer

O Bauer usa Jev apenas como decisor estruturado opcional. Jev pode classificar
uma tarefa, estimar complexidade, escolher o tier (`fast`, `balanced`, `coding`
ou `heavy`) e indicar se há necessidade de orquestração. A execução continua
passando pelo roteador de modelos, Kernel, policy, approval, allowlist e gates
do Bauer.

Jev fica desligado por padrão. Para habilitar no perfil atual:

```bash
bauer config set TYPESAFE_API_KEY sua-chave
bauer config set decision.jev_enabled true
bauer config set decision.fallback_enabled true
bauer config set model.router_enabled true
```

Os tiers precisam estar configurados em `model.profiles`, por exemplo:

```yaml
model:
  router_enabled: true
  profiles:
    fast: {provider: ollama, model: qwen3:0.6b}
    balanced: {provider: ollama, model: qwen3:8b}
    coding: {provider: ollama, model: qwen3-coder:30b}
    heavy: {provider: openrouter, model: deepseek/deepseek-r1}

decision:
  jev_enabled: true
  fallback_enabled: true
  timeout_seconds: 2.0
  min_confidence: 0.65
```

Para desligar Jev e voltar imediatamente ao classificador local:

```bash
bauer config set decision.jev_enabled false
```

Se Jev estiver habilitado, mas houver timeout, erro HTTP, resposta inválida ou
confiança abaixo do limite, `fallback_enabled: true` usa o classificador
heurístico local. Para operar sem qualquer chamada externa de decisão, deixe
`decision.jev_enabled: false`. A chave é armazenada no `.env` pelo comando de
configuração e não aparece no painel nem nos eventos.

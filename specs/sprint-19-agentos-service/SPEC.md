# SPEC — AgentOS na Beelink

## Escopo

Disponibilizar o AgentOS do Agno em `7777`, usando os mesmos agentes e times
formais já catalogados pelo Bauer. O serviço será separado do Cockpit Bauer
(`7770`) e será iniciado pelo systemd no mesmo virtualenv.

## Fora de escopo

- substituir o Kernel ou a governança do Bauer;
- mover o Cockpit Bauer de `7770`;
- inventar um novo catálogo de agentes;
- expor credenciais ou desabilitar autenticação do Bauer.

## Critérios de aceitação

1. `GET /docs` e `GET /status` do AgentOS respondem em `7777`.
2. Agentes e times do catálogo formal Bauer são registrados no AgentOS.
3. O app não inicia ao importar o módulo em testes; a inicialização ocorre no
   comando/service.
4. Falha de um spec inválido produz diagnóstico claro sem derrubar o Cockpit.
5. O serviço usa `0.0.0.0` quando publicado na Beelink e pode ser parado pelo
   systemd.

## Validação

- testes unitários da fábrica do app com catálogo mínimo;
- `ruff` nos arquivos alterados;
- smoke test local de `/status` e `/docs` após ativação na Beelink.

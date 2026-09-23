# Arquitetura

O stack continua sendo uma implantação opcional do Bauer, sem misturar o
processo governado do Kernel com o processo AgentOS. Ambos são construídos a
partir do mesmo código Python, compartilham o workspace/memória persistidos e
usam a mesma instância interna do Ollama.

```text
browser ── :3000 ── agent-ui (Next.js)
                     │ endpoint configurável
browser ── :7777 ── agentos (Agno AgentOS)
browser ── :8000 ── bauer (Kernel/Cockpit)
                     │
              ollama:11434 (somente rede Docker)
```

O Agent UI é clonado durante o build pela imagem `agent-ui/Dockerfile`. A
revisão vem de `AGENT_UI_REF`, com `main` como padrão; o operador pode fixar um
commit, tag ou branch em `.env`. O endpoint inicial da UI é aplicado no build por
`AGENT_UI_ENDPOINT`, porque a UI oficial guarda a seleção no navegador.

O instalador usa modo `auto`: em instalação nova e em atualização, se houver
`docker compose` ou `docker-compose`, ele garante arquivos de configuração
mínimos e executa `docker compose up -d --build`. A ausência de Docker apenas
produz aviso e mantém a instalação nativa. `--docker` transforma a ausência em
erro; `--no-docker` desativa o provisionamento.

O AgentOS recebe CORS explícito para a origem da UI. A origem e o endpoint
podem ser ajustados para acesso remoto por `.env`, sem gravar segredo na
imagem.

# SPEC — Sprint 51: pesquisa e desenvolvimento em modo de leitura (plano 057, sprint "19")

Depende da sprint 50 (catálogo, manifesto e ponte ToolRouter).

## Problema

Com a ponte pronta, nenhum toolkit de pesquisa/dev estava revisado. O SDK
oferece GitHub (PyGithub, não instalado no lock), MCP (`mcp`, não instalado) e
uma dúzia de toolkits web — vários buscam **URL arbitrária** com `httpx`
próprio, fora do `url_safety` do Bauer.

## Escopo

1. **Busca web** revisada como leitura (`network.http`): `agno.websearch`,
   `agno.duckduckgo` (ddgs, extra `web`), `agno.hackernews`, `agno.pubmed`,
   `agno.wikipedia` e `agno.arxiv` (só busca; dependências opcionais).
   Ações que escrevem (knowledge base, download de PDF) ficam fora.
2. **Extração de URL arbitrária** (`website`, `webtools`, `jina`,
   `newspaper*`, `trafilatura`, `crawl4ai`, `spider`, `firecrawl`, `api`,
   `llms_txt`, `brightdata`, `oxylabs`, `scrapegraph`, `webbrowser`):
   `incompatible`, apontando para `bauer.web_fetch` (agora no catálogo), que
   já aplica `url_safety`/SSRF e limites Bauer.
3. **Argumentos com segredo não saem da máquina**: para operações de saída
   (`network.http`, `mcp.call`, `integration.write`) a ponte recusa argumentos
   em que o scanner de segredos encontra material sensível. (A regra
   `network.http.secret_exfiltration.deny` do PolicyEngine não tinha produtor
   de `contains_secret`; a checagem fica na ponte, sem alterar o engine.)
4. **GitHub somente leitura**:
   - `bauer.github` — adapter nativo (REST via `httpx`, sem dependência
     nova): repositório, issues, PRs, commits, arquivo, busca **dentro de um
     repo**. Allowlist `owner/repo` obrigatória na configuração; repo fora da
     lista é negado antes da rede; qualificadores `repo:/org:/user:` são
     removidos da busca; `..` em caminho é rejeitado; timeout, HTTP de erro e
     JSON malformado viram `ToolError` sem derrubar o run.
   - `agno.github` — `GithubTools` do SDK restrito a 12 ações de leitura, com
     guarda de `repo_name` contra a mesma allowlist; `dependency_missing`
     até o operador instalar `pygithub`.
   - Escrita não é classificada ⇒ impossível de conceder (sprint 57 cria o
     primeiro conector de escrita como capability separada).
   - Credencial por nome (`credential`, default `GITHUB_TOKEN`) via env ou
     `bauer credential`; nunca no YAML, nos eventos ou na UI.
5. **MCP governado** (`bauer.mcp`): servidores e tools **enumerados pelo
   administrador**, cada tool com risco explícito; ações `<servidor>__<tool>`;
   grant padrão só `read`; `write` bloqueada (sem fluxo de aprovação);
   descrição mostrada ao modelo vem da config (metadado do servidor é não
   confiável); servidor só inicia na chamada e para ao fim; stdio com
   **ambiente mínimo** (sem herdar segredos do Bauer) + credenciais injetadas
   por nome; resultado marcado como conteúdo externo não confiável;
   `agno.mcp`/`agno.mcp_toolbox` `incompatible` (descobrem e expõem tudo).

## Fora de escopo

- Escrita em GitHub/MCP; instalação automática de PyGithub/`mcp`/servidores.
- Descoberta automática de servidores MCP ou de tools via `tools/list`.

## Decisões (sem operador — opção mais segura)

- Adapter GitHub **nativo** além do SDK: o lock não traz PyGithub e o plano
  proíbe instalar dependências automaticamente; o nativo usa só `httpx`.
- Sem credencial configurada, `bauer.github` lê só repositórios públicos da
  allowlist (a allowlist continua obrigatória).
- MCP via clientes stdlib já existentes (`bauer/mcp_client.py`,
  `mcp_http_client.py`); o pacote `mcp` do SDK não é requisito.
- URLs HTTP de MCP declaradas pelo admin não passam pelo `url_safety`
  (servidores locais são o caso comum); apenas esquema http(s) é aceito.

## Critérios de aceite

- [x] Research/Architect leem só repositórios e fontes autorizados.
- [x] Nenhuma mutação GitHub/MCP disponível.
- [x] Servidor MCP não chamado não inicia; tool não declarada não existe.
- [x] Fora da allowlist ⇒ negado antes da rede e auditável (`tool.call.failed`).
- [x] Timeout e resposta malformada não derrubam o run.
- [x] Credenciais/argumentos ausentes da telemetria.
- [x] Suíte e Ruff verdes.

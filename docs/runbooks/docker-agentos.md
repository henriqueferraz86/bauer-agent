# Stack Docker: Bauer, AgentOS e Agent UI

Runbook oficial para instalar, operar, atualizar e diagnosticar o stack Docker.

## Componentes

| Serviço | Porta | Função |
|---|---:|---|
| \`bauer-agent\` | \`8000\` | API REST e runtime governado |
| \`bauer-agentos\` | \`7777\` | AgentOS/Agno |
| \`bauer-agent-ui\` | \`3000\` | Agent UI oficial do Agno |
| \`bauer-ollama\` | interna | Modelos LLM |
| \`bauer-ollama-init\` | interna | Download inicial dos modelos |

Ollama não é publicado no host. Modelos, workspace, memória e logs persistem
em volumes nomeados.

## Pré-requisitos

- Docker Engine + Compose v2 no Linux/macOS;
- Docker Desktop com motor Linux/WSL2 no Windows;
- internet no primeiro build e no primeiro download dos modelos;
- pelo menos 8 GB de RAM recomendados para o conjunto padrão.

O stack não instala Docker no sistema operacional.

## Linux/macOS

\`\`\`bash
curl -fsSL https://raw.githubusercontent.com/henriqueferraz86/bauer-agent/master/install.sh \\
  | bash -s -- --docker --no-extra
\`\`\`

O instalador cria \`config.yaml\`, \`models.yaml\`, \`.env\`, gera
\`BAUER_SERVE_API_KEY\` e preserva esses arquivos durante atualizações.

## Windows com Docker Desktop

O \`install.ps1\` instala o Bauer nativo; para AgentOS + Agent UI, clone o
repositório e use o Compose pelo PowerShell:

\`\`\`powershell
git clone https://github.com/henriqueferraz86/bauer-agent.git "$env:USERPROFILE\\BauerAgent"
Set-Location "$env:USERPROFILE\\BauerAgent"
Copy-Item config.yaml.example config.yaml
"models: {}" | Set-Content models.yaml
Copy-Item .env.example .env
\`\`\`

Gere uma chave REST local sem publicá-la:

\`\`\`powershell
$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$key = ([BitConverter]::ToString($bytes) -replace '-', '').ToLower()
Add-Content .env "BAUER_SERVE_API_KEY=$key"
\`\`\`

Construa e suba:

\`\`\`powershell
docker compose build --pull --no-cache agent-ui
docker compose up -d --build
docker compose ps
\`\`\`

## Configuração de acesso

Na própria máquina:

\`\`\`dotenv
AGENT_UI_ENDPOINT=http://localhost:7777
AGENT_OS_CORS_ORIGINS=http://localhost:3000
\`\`\`

Pela rede, substitua o IP pelo endereço do host:

\`\`\`dotenv
AGENT_UI_ENDPOINT=http://192.168.3.65:7777
AGENT_OS_CORS_ORIGINS=http://192.168.3.65:3000,http://localhost:3000
\`\`\`

Depois de alterar \`AGENT_UI_ENDPOINT\`, reconstrua a UI:

\`\`\`bash
docker compose build --pull --no-cache agent-ui
docker compose up -d agentos agent-ui
\`\`\`

\`BAUER_SERVE_API_KEY\` é obrigatório quando Bauer faz bind em \`0.0.0.0\`.
Não publique a porta 11434 do Ollama.

## Operação

\`\`\`bash
docker compose ps
docker compose logs -f
docker compose logs --tail=100 agentos
docker compose restart
docker compose down
docker compose up -d
\`\`\`

Smoke test:

\`\`\`bash
curl -fsS http://localhost:7777/health
curl -fsS http://localhost:8000/health
curl -I http://localhost:3000
\`\`\`

## Atualização

Reiniciar reutiliza as imagens existentes. Para atualizar a \`master\`:

\`\`\`bash
git fetch origin master
git pull --ff-only origin master
docker compose build --pull --no-cache agent-ui
docker compose up -d --build
\`\`\`

No Linux/macOS, também é possível usar:

\`\`\`bash
bash install.sh --update --docker --no-extra
\`\`\`

Atualizações não removem volumes. O build da UI baixa a fonte do repositório
oficial \`agno-agi/agent-ui\`; a revisão pode ser fixada por \`AGENT_UI_REF\`.

## Remoção e persistência

\`\`\`bash
# remove contêineres e rede, preservando dados
docker compose down

# remove também modelos, workspace, memória e logs (destrutivo)
docker compose down -v
\`\`\`

Use \`down -v\` somente quando quiser apagar os dados persistentes.

## Diagnóstico

\`\`\`bash
docker compose logs --tail=120 agentos
docker compose logs --tail=120 bauer
docker inspect bauer-agentos --format '{{json .State.Health}}'
docker exec bauer-ollama ollama list
docker logs bauer-ollama-init
\`\`\`

Se AgentOS ficar unhealthy, valide \`agno[os]\` e \`config.yaml\`; em configurações
antigas, \`continuous_autonomy.targets\` deve ser \`[]\`, não \`null\`.

Se Bauer reiniciar, confirme \`BAUER_SERVE_API_KEY\` e o modelo configurado. Para
um modelo Ollama fora do conjunto padrão, defina no \`.env\`:

\`\`\`dotenv
BAUER_MODEL=qwen3:0.6b
\`\`\`

Se a UI abrir sem conversar, confira \`AGENT_UI_ENDPOINT\`,
\`AGENT_OS_CORS_ORIGINS\`, reconstrua \`agent-ui\` e teste \`/health\` do AgentOS.

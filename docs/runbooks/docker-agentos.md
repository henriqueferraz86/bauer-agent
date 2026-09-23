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

Se a instalação nativa também estiver presente, `bauer update` é independente
do Compose. No Windows, feche `bauer serve`, `bauer agent`, terminais de debug
e processos que tenham carregado o Bauer antes de atualizar. Um `Acesso negado`
em `pydantic_core\\_pydantic_core.*.pyd` significa que o Windows bloqueou a
extensão nativa enquanto ela estava em uso. Encerre somente processos ligados
à instalação e tente novamente:

\`\`\`powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -and $_.CommandLine -match 'BauerAgent|bauer' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
bauer update
\`\`\`

O comando `bauer update` restaura o snapshot se uma etapa falhar; não apague a
`.venv` como primeira tentativa. O procedimento acima é para a instalação
nativa; para este stack Docker, use a seção [Atualização](#atualização).

Se uma tentativa interrompida deixar o comando `bauer` com
`ModuleNotFoundError: No module named 'bauer'`, repare o link local sem tocar
nas dependências:

\`\`\`powershell
$root = "$env:LOCALAPPDATA\BauerAgent"
$py = "$root\.venv\Scripts\python.exe"
& $py -m pip install --no-deps --editable $root
bauer update
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

### Chave de bootstrap e clientes da API

`BAUER_SERVE_API_KEY` é a chave do servidor Bauer, não uma chave da OpenAI ou
de outro provider. No primeiro acesso ao cockpit em `http://localhost:8000`,
ela é solicitada uma única vez para autorizar o cadastro do administrador.
Depois disso o navegador usa uma sessão `HttpOnly` e não armazena a chave.

Os comandos abaixo também servem para recuperação da conta e para clientes
externos que continuam autenticando com `X-API-Key`. Trate o valor como segredo
e não o publique em screenshots, issues ou commits.

Para descobrir qual pasta criou os contêineres, execute:

```bash
docker inspect bauer-agent --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
```

#### Linux

Na instalação feita por `install.sh`, a pasta padrão é
`~/.local/share/bauer-agent`. Em um clone manual, troque o caminho abaixo pela
pasta que contém `docker-compose.yml` e `.env`.

Exibir a chave existente:

```bash
cd ~/.local/share/bauer-agent
grep '^BAUER_SERVE_API_KEY=' .env | tail -n 1 | cut -d= -f2-
```

Se a chave não existir, ou para substituí-la por uma nova:

```bash
cd ~/.local/share/bauer-agent
key="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
touch .env
sed -i '/^BAUER_SERVE_API_KEY=/d' .env
printf '\nBAUER_SERVE_API_KEY=%s\n' "$key" >> .env
chmod 600 .env
docker compose up -d --force-recreate bauer
printf '%s\n' "$key"
```

Validar a chave contra o servidor:

```bash
key="$(grep '^BAUER_SERVE_API_KEY=' .env | tail -n 1 | cut -d= -f2-)"
curl -fsS -H "X-API-Key: $key" http://localhost:8000/status
```

#### Windows (PowerShell)

Use o diretório informado pelo `docker inspect`; o exemplo abaixo também
funciona quando o Compose está em `%LOCALAPPDATA%\BauerAgent`:

```powershell
$root = docker inspect bauer-agent --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
if (-not $root) { $root = "$env:LOCALAPPDATA\BauerAgent" }
Set-Location $root
```

Exibir a chave existente:

```powershell
$line = Get-Content .env | Where-Object { $_ -match '^BAUER_SERVE_API_KEY=.+' } | Select-Object -Last 1
if (-not $line) { throw 'BAUER_SERVE_API_KEY não encontrada no .env' }
$key = $line -replace '^BAUER_SERVE_API_KEY=', ''
$key
```

Se a chave não existir, ou para substituí-la por uma nova:

```powershell
$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
$key = ([BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
$envFile = Join-Path (Get-Location) '.env'
$lines = @(Get-Content $envFile -ErrorAction SilentlyContinue |
    Where-Object { $_ -notmatch '^BAUER_SERVE_API_KEY=' })
[IO.File]::WriteAllLines($envFile, $lines, [Text.UTF8Encoding]::new($false))
Add-Content -LiteralPath $envFile -Value "BAUER_SERVE_API_KEY=$key"
docker compose up -d --force-recreate bauer
$key
```

Validar a chave contra o servidor:

```powershell
Invoke-RestMethod http://localhost:8000/status -Headers @{ 'X-API-Key' = $key }
```

No primeiro cadastro, cole somente o valor depois de
`BAUER_SERVE_API_KEY=` no campo **Chave de bootstrap**. O fluxo completo de
cadastro, login e Google está em [Autenticação do frontend do serve](serve-auth.md).

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

No Windows, se os logs mostrarem \`IsADirectoryError\` para
\`/app/config.yaml\` ou \`/app/models.yaml\`, uma execução anterior criou uma
pasta no lugar do arquivo ausente. Pare o stack, renomeie ou remova somente
essas pastas vazias, recrie os arquivos conforme a seção
[Windows com Docker Desktop](#windows-com-docker-desktop) e execute
\`docker compose up -d --force-recreate\`. O Compose atual bloqueia novas
ocorrências com \`create_host_path: false\`.

Se Bauer reiniciar, confirme \`BAUER_SERVE_API_KEY\` e o modelo configurado. Para
um modelo Ollama fora do conjunto padrão, defina no \`.env\`:

\`\`\`dotenv
BAUER_MODEL=qwen3:0.6b
\`\`\`

Se a UI abrir sem conversar, confira \`AGENT_UI_ENDPOINT\`,
\`AGENT_OS_CORS_ORIGINS\`, reconstrua \`agent-ui\` e teste \`/health\` do AgentOS.

# Autenticação do frontend do `bauer serve`

O cockpit do Bauer (`http://localhost:8000`) possui um único administrador.
No primeiro acesso, escolha cadastro com e-mail/senha ou Google. Depois, o
navegador autentica automaticamente com uma sessão segura; não é necessário
copiar `X-API-Key` novamente.

## Modelo de segurança

- `BAUER_SERVE_API_KEY` autoriza somente o primeiro cadastro e a recuperação;
- a senha é armazenada como hash Argon2id, nunca em texto puro;
- a sessão usa cookie `HttpOnly` e proteção CSRF;
- a API key não é enviada ao frontend nem gravada em `localStorage`;
- CLI e integrações externas continuam usando `X-API-Key` normalmente;
- existe somente um administrador por instância.

## Primeiro cadastro

1. Suba o `bauer serve` e abra `http://localhost:8000`.
2. Informe e-mail e uma senha com pelo menos 12 caracteres.
3. Cole no campo **Chave de bootstrap** somente o valor depois de
   `BAUER_SERVE_API_KEY=`.
4. Clique em **Criar conta**.

Para localizar ou gerar a chave no Linux e Windows, use os comandos do
[runbook Docker](docker-agentos.md#chave-de-bootstrap-e-clientes-da-api).

O cadastro fecha assim que o administrador é criado. Outra requisição de setup
não consegue criar uma segunda conta, inclusive quando chega simultaneamente.

## Login com e-mail e senha

Depois do cadastro, abra o cockpit e informe o mesmo e-mail e senha. A sessão
permanece válida pelo período definido em `serve.auth_session_hours`:

```yaml
serve:
  web_auth_enabled: true
  auth_session_hours: 168
```

Para sair, abra **Config → Conta da interface → Sair**.

## Login com Google

O Bauer usa Google Identity Services apenas para confirmar identidade. Ele não
solicita acesso ao Drive, Gmail ou outras APIs e não armazena o ID token.

### 1. Criar o Client ID

No Google Cloud Console:

1. configure a tela de consentimento;
2. crie uma credencial **OAuth client ID → Web application**;
3. adicione a origem JavaScript do cockpit, por exemplo:
   `http://localhost:8000`;
4. para acesso remoto, adicione a origem HTTPS pública exata.

Não é necessário fornecer Client Secret ao Bauer.

### 2. Configurar no Linux

Instalação padrão:

```bash
cd ~/.local/share/bauer-agent
printf '\nBAUER_AUTH_GOOGLE_CLIENT_ID=%s\n' 'SEU_CLIENT_ID.apps.googleusercontent.com' >> .env
docker compose up -d --force-recreate bauer
```

Em execução nativa, defina a variável antes de iniciar:

```bash
export BAUER_AUTH_GOOGLE_CLIENT_ID='SEU_CLIENT_ID.apps.googleusercontent.com'
bauer serve --config config.yaml
```

### 3. Configurar no Windows PowerShell

Para o Compose ativo:

```powershell
$root = docker inspect bauer-agent --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
Set-Location $root
Add-Content .env "BAUER_AUTH_GOOGLE_CLIENT_ID=SEU_CLIENT_ID.apps.googleusercontent.com"
docker compose up -d --force-recreate bauer
```

Em execução nativa:

```powershell
$env:BAUER_AUTH_GOOGLE_CLIENT_ID = 'SEU_CLIENT_ID.apps.googleusercontent.com'
bauer serve --config config.yaml
```

### 4. Cadastrar ou vincular

- Instância sem administrador: informe a chave de bootstrap e clique no botão
  Google na tela de cadastro.
- Administrador local existente: entre com senha, abra **Config** e vincule a
  conta Google com o mesmo e-mail.
- Depois de vinculada, a conta Google pode ser usada diretamente no login.

## Recuperar ou definir senha

Na tela de login, clique em **Esqueci minha senha**, informe o e-mail, a nova
senha e a chave de bootstrap. A recuperação revoga todas as sessões anteriores.

Uma conta criada somente com Google também pode definir sua primeira senha por
esse fluxo.

## API e automações externas

O login web não remove a autenticação tradicional:

```bash
curl -H "X-API-Key: $BAUER_SERVE_API_KEY" http://localhost:8000/status
```

Não use cookies da sessão em scripts. Para integrações, continue usando
`X-API-Key` ou `Authorization: Bearer`.

## Reverse proxy e HTTPS

Em acesso remoto, configure `serve.deployment_mode: reverse_proxy`,
`serve.public_url`, CORS e proxies confiáveis conforme o runbook. Nesse modo o
cookie de sessão recebe `Secure` e só funciona sobre HTTPS.

## Desabilitar o login web

```yaml
serve:
  web_auth_enabled: false
```

Isso mantém `X-API-Key` para API/CLI, mas bloqueia o login do cockpit. Use essa
opção somente quando a interface web não deve ser utilizada.

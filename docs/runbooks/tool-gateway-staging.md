# Staging offline do Tool Gateway

Para testar apenas o AgentOS, use `deploy/tool-gateway-staging.compose.yml` sozinho, a partir da raiz do repositório. O serviço `bauer` mantém o namespace de rede do ensaio e o AgentOS do canal escuta em `127.0.0.1:7787` dentro dele. O Compose usa `network_mode: none`, volumes distintos e um modelo de teste que funciona offline. O teste com o servidor Bauer e o Tool Gateway usa também o overlay abaixo.

## Tool Gateway ativo no canário offline da Beelink

A instalação em `~/.local/share/bauer-tool-gateway-canary` usa os dois arquivos Compose de staging e canário. O overlay sobe o servidor Bauer pela fábrica real, ativa a rota do Tool Gateway e usa um AgentOS dedicado com um modelo determinístico. A prova cria um turno sintético no executor HTTP do Telegram sob custódia do Kernel, valida leitura e retomada por HTTP real e confirma o ledger. O bot real e os serviços de produção não fazem parte desse ambiente.

```bash
cd ~/.local/share/bauer-tool-gateway-canary
./canary-compose ps
./canary-compose exec -T bauer python /app/tool-gateway-canary.py probe
# Para parar o canário e preservar estado e chaves:
./canary-compose down
# Para subir novamente:
./canary-compose up -d --wait --wait-timeout 95
```

Os serviços não publicam portas e compartilham apenas o namespace isolado de rede. O arquivo `.env.canary` tem credenciais descartáveis com permissão `0600`. As chaves RSA ficam no volume do Bauer de teste. Renderize o Compose com `config --quiet` para evitar imprimir segredos.

## Teste a partir da imagem candidata

Selecione a imagem candidata já construída e identificada no registro do teste. Este ensaio usa chaves descartáveis e não carrega o `.env` da instalação. Em Linux, execute em um shell dedicado:

```bash
export BAUER_STAGING_IMAGE=bauer-tool-gateway:candidato
export BAUER_STAGING_JWT_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
export BAUER_STAGING_REQUIREMENT_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
export BAUER_STAGING_CONTINUATION_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')

staging_compose() {
  docker compose --env-file /dev/null -f deploy/tool-gateway-staging.compose.yml "$@"
}

staging_compose config -q
staging_compose up -d --wait --wait-timeout 90
staging_compose exec -T bauer python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:7787/health', timeout=5).status == 200"
```

Verifique o contrato autenticado do AgentOS pelo mesmo loopback. O teste exige recusa sem JWT, capacidades assinadas, run de texto concluída, recusa de acesso cruzado e bloqueio de `/continue` genérico. Quando a versão publica o modo de autenticação e o isolamento no `/info`, confira também esses campos.

```bash
staging_compose exec -T agentos-channel python - <<'PY'
import os
import secrets

import httpx
import jwt

from bauer.agentos_capabilities import verify_capability_proof
from bauer.agentos_jwt_issuer import AgentOSJWTIssuer

agent = 'bauer.dev'
scopes = [f'agents:{agent}:read', f'agents:{agent}:run']
key = os.environ['BAUER_AGENTOS_JWT_KEY']
issuer = AgentOSJWTIssuer(
    key=key,
    issuer=os.environ['BAUER_AGENTOS_JWT_ISSUER'],
    audience=os.environ['BAUER_AGENTOS_JWT_AUDIENCE'],
    allowed_scopes=scopes,
)
token = issuer.issue(authenticated_user_id='1', scopes=scopes)
subject = jwt.decode(token, options={'verify_signature': False})['sub']
challenge = secrets.token_hex(32)
session = secrets.token_hex(16)

with httpx.Client(base_url='http://127.0.0.1:7787', trust_env=False, timeout=10) as client:
    info = client.get('/info').raise_for_status().json()
    if 'user_isolation' in info:
        assert info['user_isolation'] is True
    if 'auth_mode' in info:
        assert info['auth_mode'] != 'none'
    assert client.get('/bauer/channel-capabilities').status_code == 401
    auth = {'Authorization': f'Bearer {token}'}
    proof = client.get('/bauer/channel-capabilities', headers={**auth, 'X-Bauer-Challenge': challenge})
    signed = proof.raise_for_status().json()
    caps = signed['capabilities']
    assert verify_capability_proof(key, challenge, caps, signed['proof'])
    assert caps['identity'] == {'subject': subject, 'scopes': sorted(scopes)}
    assert caps['user_isolation'] is True
    assert caps['external_read_tools'] is True and caps['catalog_tools'] is False
    assert caps['tool_continuation_version'] == 2
    run = client.post(f'/agents/{agent}/runs', headers=auth,
                      data={'message': 'staging offline', 'session_id': session, 'stream': 'false'})
    outcome = run.raise_for_status().json()
    assert outcome['status'].upper() == 'COMPLETED'
    run_path = f'/agents/{agent}/runs/{outcome["run_id"]}'
    other = issuer.issue(authenticated_user_id='2', scopes=scopes)
    assert client.get(run_path, headers={'Authorization': f'Bearer {other}'},
                      params={'session_id': session}).status_code in {403, 404}
    assert client.post(f'{run_path}/continue', headers=auth,
                       data={'session_id': session}).status_code == 403

print('AgentOS de staging: saúde, autenticação, fronteira e run offline confirmadas')
PY
```

Valide o gateway e a chamada de leitura no código da mesma revisão da imagem. A suíte abre dois servidores HTTP no loopback e usa um projeto e uma base do Kernel temporários.

```bash
uv sync --frozen --extra dev
uv run pytest -o addopts='' tests/test_telegram_tool_gateway_real_http.py \
  tests/test_tool_gateway_network_e2e.py tests/test_agentos_external_execution_proof.py \
  -q --tb=short
staging_compose down
```

Para preparar o canário na Beelink, mantenha uma instância AgentOS de canal em namespace compartilhado com o Bauer, com porta apenas no loopback, base de estado própria e credenciais de serviço separadas. O Gateway Telegram e o `bauer serve` executam no mesmo container e podem se comunicar por `http://127.0.0.1:8000`. Valide que a instância do canal usa `http://127.0.0.1:7787` e que o Agent UI continua usando seu destino existente. O Agent UI usa rotas que a fronteira do canal bloqueia e requer um caminho de acesso autenticado próprio.

O `bauer serve` inicializa `telegram-agentos.sqlite3` no diretório de sessões ao habilitar o router. Confirme que o processo do Telegram usa esse mesmo diretório e verifique a política de leitura, o projeto de teste, os mounts e o backup recente por `quick_check`. Um canário com bot separado requer token e estado próprios. Com o bot atual de um único usuário permitido, o toggle global muda o caminho de todo o tráfego desse bot. Siga as fases e os critérios registrados no plano da sprint para decidir o corte.

# Reprodução da auditoria

Os scripts de prova são evidências de bugs, não testes pytest que passam ao
corrigir o produto. Eles imprimem resultados; exit 0 sozinho não valida o
conteúdo. Consulte os logs correspondentes em `../evidence/`.

## Provas herméticas

Na raiz do repositório:

```bash
uv sync --frozen --extra dev
uv run python docs/audits/2026-10-12/repro/bauer-audit-kernel-repro.py
uv run python docs/audits/2026-10-12/repro/bauer_audit_tools_probe.py
uv run python docs/audits/2026-10-12/repro/bauer_audit_tools_probe_extra.py
uv run python docs/audits/2026-10-12/repro/bauer_web_repro2.py
uv run python docs/audits/2026-10-12/repro/bauer_web_crossreview_history.py
uv run python docs/audits/2026-10-12/repro/bauer_web_crossreview_kernel.py
uv run python docs/audits/2026-10-12/repro/bauer_web_openai_parity.py
```

Cada script cria diretório temporário e isola BAUER_HOME/CONFIG/AGENTS_FILE
antes de importar runtime. Não usa credenciais de provider ou envia mensagens
externas. O probe de tools roda somente `printf` inofensivo para provar o
bypass de cron. O script histórico original também foi preservado, porém
`bauer_web_crossreview_history.py` tem o patch corretamente instalado antes
de `create_app` e provas com Kernel ligado.

## Navegador

Playwright é auxiliar, fora do lock do Bauer. Na execução original:

```bash
uv run --no-project --with playwright==1.63.0 python SCRIPT.py
```

Os scripts registram JSON, screenshots e traces. O caminho do Chromium em
`launch(executable_path=...)` corresponde ao cache desta máquina; adapte-o
ao seu navegador Playwright instalado. Nenhuma dependência do projeto precisa
ser alterada para instalar a ferramenta auxiliar.

### Instância com provider controlado

Em um terminal, da raiz do repo:

```bash
uv run python docs/audits/2026-10-12/repro/controlled_server.py
```

Em outro:

```bash
uv run --no-project --with playwright==1.63.0 python docs/audits/2026-10-12/repro/browser_controlled.py
```

Servidor em `127.0.0.1:18761`; dados em
`/tmp/bauer-audit-20261012/controlled`. Pare o processo ao concluir. Esse
provider não faz inferência real: devolve tool calls determinísticas. Kernel,
ToolRouter, filesystem, HTTP, SSE e UI são os do produto.

### Instância com Ollama real

Prepare um diretório temporário com home/workspace/sessions e config com:

- `model.provider: ollama`, inicialmente `name: qwen2.5-coder:3b`,
  requested_context 8192/minimum_context 4096;
- `ollama.host`: endpoint Ollama seu, com os modelos já instalados;
- `agent.workspace`: workspace temporário;
- tools allowlist `[read_file, list_dir, write_file, datetime_now, calculate]`,
  shell/web desabilitados;
- serve host 127.0.0.1, port 18760, API key **fictícia de teste**
  `audit-bootstrap-only`, rate_limit_requests 0;
- logging em arquivo temporário e nível `debug`;
- workspace/audit_marker.txt contendo `BAUER_BROWSER_AUDIT_20261012`.

Não use config/home/sessões reais. Inicie pelo CLI com config, models ausente,
workspace, sessions-dir e state-file explicitamente temporários. Exemplo após
preparar `/tmp/bauer-audit-20261012`:

```bash
BAUER_HOME=/tmp/bauer-audit-20261012/home \
BAUER_CONFIG=/tmp/bauer-audit-20261012/config.yaml \
BAUER_AGENTS_FILE=/tmp/bauer-audit-20261012/no-agents.yaml \
BAUER_OLLAMA_READ_TIMEOUT=180 \
uv run bauer serve --config /tmp/bauer-audit-20261012/config.yaml \
  --models /tmp/bauer-audit-20261012/no-models.yaml \
  --workspace /tmp/bauer-audit-20261012/workspace \
  --sessions-dir /tmp/bauer-audit-20261012/sessions \
  --state-file /tmp/bauer-audit-20261012/state.json
```

Execute `browser_real.py` uma vez para cadastrar a conta fictícia. Depois,
`browser_real_followup.py` usa login e troca para qwen3-coder:30b; finalmente
`browser_real_write.py` comprova escrita/leitura e divergência do HUD.
Credenciais nos scripts são exclusivamente fixtures desta conta descartável.
Não rodar esses scripts contra um servidor compartilhado ou de produção.

Reexecução deve usar recursos temporários próprios. Para repetir cadastro,
prepare uma raiz nova e adapte ROOT/BASE nos scripts em vez de remover dados
de uma instância existente. Modelos, latência e resultados variam por máquina.

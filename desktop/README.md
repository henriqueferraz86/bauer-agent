# Bauer Agent Desktop

Interface gráfica do Bauer, em duas camadas:

- **Fase 1 — Web SPA** (`src/`): React + Vite, 8 telas (Projetos, Chat, Kanban,
  Modelos, Gateway, Observabilidade, Logs, Config). É servida pelo próprio
  `bauer serve` em `/` (o `vite build` emite para `../bauer/static/`).
- **Fase 2 — App nativo** (`src-tauri/`): shell Tauri v2 que abre a SPA numa
  janela própria, **sem depender do browser**.

A SPA conversa com o backend via HTTP/SSE; o backend é o `bauer serve` existente.

## Pré-requisitos

- **Node 20+** (frontend)
- **Python** com o pacote `bauer` instalado (o app spawna `python -m bauer.cli serve`)
- App nativo: **Rust** (stable) e, no Windows, **WebView2** (já vem no Win11)

## Web SPA (Fase 1)

```bash
npm install

# Dev (Vite :5173, faz proxy de /api,/stream → bauer serve em :8000)
npm run dev          # num terminal
bauer serve --port 8000   # noutro

# Build de produção → emite para ../bauer/static/ (servido pelo bauer serve)
npm run build
```

Ou, sem dev server, pelo próprio CLI: `bauer desktop` sobe o serve como sidecar
e abre a SPA no navegador.

## App nativo (Fase 2 — Tauri)

```bash
# Dev: abre a janela nativa, spawna o bauer serve e carrega as 8 telas
npm run tauri:dev

# Build do bundle nativo (.exe/.msi, .dmg, .deb/.AppImage conforme o SO)
npm run tauri:build
# saída em src-tauri/target/release/bundle/
```

### Como o app nativo funciona

No boot, o processo Rust (`src-tauri/src/lib.rs`):

1. lê `~/.bauer/projects.json` e escolhe o **projeto ativo** (fallback: `$HOME`);
2. acha uma **porta livre** e localiza o **Python**;
3. spawna `python -m bauer.cli serve` com `cwd` = projeto ativo (para achar
   `config.yaml`/`.env`);
4. aguarda `GET /health` responder e então navega a janela para
   `http://127.0.0.1:<porta>/`;
5. ao fechar o app, **encerra o processo do serve** (sem órfãos).

### Variáveis de ambiente

- `BAUER_PYTHON` — caminho do interpretador Python a usar. Defina isto se o `bauer`
  estiver num venv (ex.: `BAUER_PYTHON=C:\...\.venv\Scripts\python.exe`). Sem ela,
  o app tenta `py`/`python`/`python3` e prefere o que tiver `import bauer`.

### Se a janela ficar em "Iniciando…"

Significa que o serve não respondeu. Causas comuns: Python sem o pacote `bauer`
(defina `BAUER_PYTHON`), projeto ativo sem `config.yaml`, ou provider/Ollama
offline. A tela de erro do app aponta isso.

## Releases & auto-update

O app se atualiza sozinho via `tauri-plugin-updater` + **GitHub Releases**. No boot, ele
consulta a release `latest`, e se houver versão nova pergunta "Instalar e reiniciar agora?".
Os artefatos de update são assinados com uma chave **minisign** própria (≠ code-signing) —
mesmo sem assinatura de código, o update é verificado contra adulteração.

### Cortar uma release

```bash
# 1) bump da versão (fonte: src-tauri/tauri.conf.json -> "version")
# 2) tag + push  → dispara .github/workflows/release.yml
git tag v0.2.0
git push origin v0.2.0
```

O workflow builda nos 3 SOs, gera os bundles + `latest.json` e cria uma **GitHub Release
DRAFT**. Revise e **publique** a release — só então o `latest.json` fica acessível em
`releases/latest/download/latest.json` e os apps existentes detectam a atualização.

`workflow_dispatch` (Actions → Release → Run) builda sem criar release — útil para validar
o pipeline.

### Secrets necessários (uma vez, no repositório)

A chave **privada** do updater foi gerada em `~/.tauri/bauer-updater.key` (fora do repo,
nunca commitada). Adicione como secrets do GitHub:

```bash
gh secret set TAURI_SIGNING_PRIVATE_KEY < ~/.tauri/bauer-updater.key
gh secret set TAURI_SIGNING_PRIVATE_KEY_PASSWORD --body ""   # senha vazia (como gerada)
```

A chave **pública** já está em `src-tauri/tauri.conf.json` (`plugins.updater.pubkey`).

### Caveats

- **macOS sem code-signing**: o auto-update é frágil (o Gatekeeper pode barrar o `.app`
  atualizado/quarentena). **Windows e Linux** atualizam bem unsigned. Resolver de vez exige
  code-signing (Authenticode + Apple Developer ID + notarização) — fase futura.
- Os instaladores são **unsigned** por ora: o SmartScreen/Gatekeeper avisam na 1ª instalação.

## Testes

```bash
cd src-tauri && cargo test --lib   # projeto ativo, porta, python, should_update (semver)
```

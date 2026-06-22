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

## Testes

```bash
cd src-tauri && cargo test --lib   # lógica Rust (projeto ativo, porta, python)
```

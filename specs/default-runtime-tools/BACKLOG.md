# BACKLOG — Ferramentas padrão do runtime

## Tarefas

### 1. Inventariar ferramentas e dependências instaladas

Status: concluído

Critérios:
- Pacotes inspecionados no Docker Windows e no venv ativo da Beelink.
- Dependências Python separadas de utilitários de host e modelos opcionais.

### 2. Declarar runtime Python padrão e atualizar lock

Status: concluído

Critérios:
- Capacidades centrais ficam declaradas em `dependencies`.
- Extras seguem compatíveis.
- `uv.lock` validado.

### 3. Instalar Chromium nos fluxos de instalação

Status: concluído

Critérios:
- Scripts para Windows e Linux.
- Docker inclui navegador e bibliotecas requeridas.
- Documentação indica comandos e modo headless em servidor.

### 4. Validar e revisar

Status: concluído

Critérios:
- `uv lock --check` passou.
- `uv sync --frozen --dry-run --extra dev` resolve as novas dependências.
- Chromium headless iniciou na Beelink e carregou a UI local com HTTP 200.
- Instruções cruzadas com Docker e manual.

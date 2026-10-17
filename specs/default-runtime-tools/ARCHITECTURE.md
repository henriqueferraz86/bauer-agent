# ARCHITECTURE — Ferramentas padrão do runtime

## Visão geral

O metadata de dependências em `pyproject.toml` será a fonte do runtime Python
padrão. Extras continuam servindo para conjuntos adicionais, pesados ou
específicos. Playwright é uma biblioteca Python; Chromium é um artefato binário
separado, instalado pelo fluxo de setup para respeitar a versão do Playwright.

## Inventário observado (2026-10-17)

| Capacidade/pacote | Docker Bauer no Windows | Venv ativo da Beelink | Decisão |
|---|---|---|---|
| Busca web (`ddgs`, `beautifulsoup4`) | instalado | instalado | já era base |
| HTTP server (`fastapi`, `uvicorn`) | instalado | instalado | passa a ser base declarada |
| Gateway (`websockets`) | instalado | instalado | passa a ser base declarada |
| Agno, SQLAlchemy, OpenAI | instalado | instalado | já era base |
| Playwright + Chromium | presentes no runtime/container | instalados durante este atendimento; browser abriu headless | base + setup de browser |
| Pillow (fallback GIF) | ausente na inspeção | ausente na inspeção | passa a ser base declarada |
| keyring | ausente na inspeção | ausente na inspeção | passa a ser base declarada |
| áudio/STT (`sounddevice`, `numpy`, `soundfile`, `faster-whisper`) | ausente | ausente | extra `[voice]` |
| SDK Anthropic, Ollama e MCP | ausentes | ausentes | não são necessários: providers usam HTTP e MCP tem cliente nativo |

No host Beelink também estavam disponíveis `git`, `gh`, `node`, `npm`,
`docker` e `ffmpeg`. São ferramentas de sistema para tarefas específicas e não
dependências Python do Bauer; o pacote não deve instalar ou atualizar esses
binários automaticamente.

## Componentes

- `pyproject.toml`: dependências base e extras compatíveis.
- `uv.lock`: versões reproduzíveis para `uv`.
- `scripts/install-browser.ps1` e `scripts/install-browser.sh`: browser local.
- `Dockerfile`: browser e bibliotecas Linux na imagem.
- `README.md`: fluxo recomendado e inventário do que é opcional.

## Decisões

- Server, gateway, autenticação segura, providers e browser entram no conjunto
  padrão por serem capacidades centrais já oferecidas pela aplicação.
- Captura de áudio/STT/TTS fica em extras por depender de hardware, modelos
  grandes e, no caso de XTTS, licença específica.
- Utilitários de projeto (Git, Node, Docker, ffmpeg e afins) não são
  dependências do pacote Bauer; são opcionais por tarefa ou infraestrutura.
- O browser instala-se explicitamente junto ao setup, não em import ou no
  primeiro uso do Bauer.

## Fluxo

```text
instalar pacote/dependências → instalar Chromium compatível → iniciar Bauer
```

## Riscos e limites

- Chromium adiciona centenas de MB ao setup.
- Linux pode precisar de bibliotecas de sistema; o Dockerfile instala-as na
  imagem. Hosts Linux locais devem seguir o comando de dependências do
  Playwright quando o navegador indicar bibliotecas ausentes.
- Em host sem desktop, o Chromium roda headless; isso não cria uma janela
  gráfica visível.

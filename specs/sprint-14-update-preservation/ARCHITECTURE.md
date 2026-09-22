# ARCHITECTURE — atualização sem perda de ambiente

`bauer update` trata o repositório e o estado do usuário como superfícies diferentes. Antes do fetch/reset, cria um snapshot em memória dos arquivos pequenos e mutáveis do usuário: `config.yaml`, `.env`, `models.yaml`, `agents.yaml`, `.runtime_state.json` e Markdown de `memory/`. O snapshot inclui arquivos no `BAUER_HOME` e equivalentes na raiz da instalação.

A atualização segue quatro fases:

1. **Snapshot:** resolve o commit atual e os arquivos preservados.
2. **Aplicação:** busca `origin/master`, aplica o reset e executa `uv sync --frozen` com os extras selecionados.
3. **Restauração e verificação:** restaura os arquivos, confirma hashes byte a byte e executa um smoke test de importação.
4. **Rollback:** em qualquer falha após o reset, volta ao commit anterior e restaura o snapshot antes de reportar erro.

O snapshot não inclui workspace, pesos de modelos ou logs, que podem ser grandes e já ficam fora do repositório. Segredos nunca são impressos.

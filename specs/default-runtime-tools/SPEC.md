# SPEC — Ferramentas padrão do runtime

## Problema

As instalações base não incluem todas as bibliotecas Python necessárias às
capacidades já anunciadas pelo Bauer. Playwright é um exemplo: a tool está
registrada, mas falha quando o pacote ou o navegador Chromium não está no
ambiente. As dependências também diferem entre `uv sync`, extras, instalação
manual e imagem Docker.

## Objetivo

Definir um conjunto padrão coerente para novas instalações do Bauer e tornar
explícita a instalação do browser Chromium, mantendo capacidades pesadas ou
dependentes de hardware como extras.

## Escopo

- Inventariar dependências Python das ferramentas registradas e conferir
  instalações de produção na Beelink e no Docker Windows.
- Tornar padrão as dependências Python pequenas e necessárias às capacidades
  centrais e ao servidor/gateway do Bauer.
- Incluir Playwright e Chromium nos fluxos de instalação local e Docker.
- Manter extras opcionais para recursos pesados/de hardware, documentando-os.
- Atualizar `uv.lock` e instruções de instalação.

## Fora de escopo

- Instalar utilitários de desenvolvimento de projetos (Node, Git, Docker, etc.)
  no host do usuário.
- Baixar modelos de voz ou LLM automaticamente.
- Abrir browser visível em servidores sem sessão gráfica.
- Modificar as instalações ativas da Beelink ou do Docker durante esta mudança.

## Requisitos

1. `uv sync` e `pip install .` instalam as dependências do runtime padrão.
2. O runtime padrão declara Playwright e os pacotes usados pelo servidor,
   gateway, integração Agno e providers implementados.
3. O fluxo de setup local baixa o browser Chromium compatível com Playwright.
4. A imagem Docker inclui Chromium e as bibliotecas de sistema necessárias.
5. STT/TTS offline e captura de microfone continuam opcionais.
6. Extras existentes permanecem compatíveis.

## Critérios de aceite

- `pyproject.toml` descreve o runtime padrão sem depender de extras ocultos.
- `uv.lock` está sincronizado com o metadata.
- As instruções cobrem Windows, Linux e Docker para instalar o browser.
- Playwright consegue iniciar Chromium headless numa instalação nova seguindo
  as instruções.
- A tabela diferencia dependências Python, browser binário e ferramentas
  externas/modelos opcionais.

## Skills obrigatórias

- `spec-driven-project-setup`

## Sub-agents recomendados

- Nenhum; escopo restrito ao metadata de dependências e instalação.

## Plano de validação

- `uv lock --check`
- Resolver/sincronizar o ambiente com o fluxo oficial do repositório.
- Conferir imports e disponibilidade de Chromium no ambiente de instalação.
- Revisar Dockerfile e comandos documentados para consistência.

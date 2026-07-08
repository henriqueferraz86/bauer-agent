# Integração: Impeccable

Repo: https://github.com/pbakaus/impeccable (Apache-2.0, autor Paul Bakaus)

Duas peças independentes, nenhuma delas exige mudança de código no Bauer.

## 3a. Detector como `verify_command` de loop-skill

`npx impeccable detect` roda 45 checagens determinísticas anti-padrão-de-IA
(gradiente roxo, fonte Inter genérica, cards aninhados, contraste baixo etc.)
**sem chamar LLM** — puramente estático. Confirmado no código-fonte
(`cli/engine/cli/main.mjs`, `cli/bin/cli.js`):

- Achou problema → `exit code 2`
- Limpo → `exit code 0`
- `--help` → `exit code 0`
- Comando desconhecido → `exit code 1`

Esse é exatamente o contrato que o gate de verificação do `/loop` (bauer/agent.py
`_run_loop_skill_verification`) já espera de um `verify_command`: `rc == 0` é
sucesso, qualquer outro código é falha. Funciona **hoje**, sem nenhuma mudança
no Bauer — é só usar.

### Exemplo pronto pra copiar

Crie `~/.bauer/loop_skills/build-ui-clean.yaml`:

```yaml
name: build-ui-clean
description: Constrói UI e verifica anti-padrões de design com o Impeccable antes de declarar concluído.
trigger_pattern: "(?i)constr[oó]i.*(tela|componente|p[aá]gina)"
task_template: "Construa a UI pedida seguindo boas práticas de design."
verify_command: "npx impeccable detect --json ."
max_minutes: 20
```

Com esse arquivo instalado, qualquer pedido que case o `trigger_pattern` dispara
um `/loop` autônomo cujo critério de "terminei de verdade" inclui passar no
detector do Impeccable — se falhar, o loop tenta corrigir uma vez (rodada
extra bounded, já implementada) antes de desistir.

`npx impeccable detect --json .` também aceita `.impeccable/config.json` /
`.impeccable/config.local.json` pra ignorar regras específicas (`ignoreRules`),
arquivos (`ignoreFiles`) ou valores (`ignoreValues`) — útil se algum
anti-padrão for intencional no projeto.

Isso **não** é uma loop-skill pré-instalada pelo Bauer — é um exemplo
documentado que o usuário copia se quiser. Mantém a regra de "zero
loop-skills instaladas por padrão" (ver plano do `/loop`).

## 3b. Conteúdo de design portado

Ver `bauer/data/skills/design/impeccable-*.yaml` — princípios gerais e fluxo
de revisão adaptados de `skill/SKILL.src.md` + `skill/reference/*.md` do
repositório original (confirmado: markdown puro, não build artifact).

## Requisitos

- Node.js >= 24 (`"engines": {"node": ">=24"}` no `package.json` do Impeccable)
- `npx` disponível no PATH de onde o `/loop`/loop-skill roda

## Como re-verificar (se algo parecer quebrado)

```bash
npx impeccable detect --version   # confirma que ainda instala/roda
npx impeccable detect --json .    # roda num diretório de teste, confere schema do JSON
echo $?                            # 0 = limpo, 2 = achou problema, 1 = erro de comando
```

Se o exit code mudar de comportamento numa versão nova, o gate de verificação
do `/loop` vai interpretar errado — reabra `bauer/agent.py::_run_loop_skill_verification`
e ajuste a checagem `proc.returncode == 0` se necessário.

## Versão verificada

`skill-v3.9.1` (guia/skill) / `cli-v3.2.0` (CLI do detector) — pesquisado em
2026-07-01. Ver `bauer/data/external_integrations.yaml`.

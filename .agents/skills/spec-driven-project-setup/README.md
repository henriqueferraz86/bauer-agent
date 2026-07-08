# spec-driven-project-setup

Skill para Claude Code.

## Instalação no projeto

Copie a pasta `spec-driven-project-setup` para:

```text
.seu-projeto/.claude/skills/spec-driven-project-setup/
```

Estrutura final:

```text
.claude/
  skills/
    spec-driven-project-setup/
      SKILL.md
```

## Uso

Dentro do Claude Code:

```text
/spec-driven-project-setup
```

Ou:

```text
Use a skill spec-driven-project-setup para configurar este projeto com skills, specs e sub-agents.
Antes de alterar arquivos, mostre o plano.
```

Ou:

```text
Use a skill spec-driven-project-setup neste projeto já existente.

Antes de alterar qualquer arquivo:

1. Analise a estrutura atual do projeto.
2. Identifique linguagem, framework, pastas, Docker, testes e documentação existente.
3. Verifique se já existe CLAUDE.md, specs/, .claude/, README ou docs.
4. Não sobrescreva arquivos existentes sem avisar.
5. Proponha um plano de adaptação.
6. Crie apenas o que estiver faltando.
7. Se algum arquivo já existir, sugira merge em vez de substituir.

Objetivo:

Adaptar este projeto para usar:

- Spec Driven Development
- Skills reutilizáveis
- Specs por feature
- Sub-agents especializados
- BACKLOG por feature

Não implemente código ainda.
```
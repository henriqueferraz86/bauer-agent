---
name: spec-driven-project-setup
description: Use para estruturar projetos com skills, specs, sub-agents e fluxo Spec Driven Development no Codex.
---

# Skill — Spec Driven Project Setup

Esta skill cria um padrão de projeto para trabalhar com:

- Skills reutilizáveis
- Specs referenciando skills
- Sub-agents especializados
- Fluxo Spec Driven Development

Fluxo principal:

```text
Ideia → SPEC.md → ARCHITECTURE.md → BACKLOG.md → Implementação → Testes → Revisão → V1
```

---

# 1. Objetivo

Use esta skill quando precisar estruturar ou evoluir um projeto para que o Codex trabalhe com padrão, rastreabilidade e entregas pequenas.

O objetivo é garantir que toda feature tenha:

1. SPEC.md
2. ARCHITECTURE.md
3. BACKLOG.md
4. Skills obrigatórias
5. Sub-agents recomendados
6. Critérios de aceite
7. Plano de validação
8. Revisão antes da entrega

---

# 2. Estrutura recomendada

Crie ou mantenha esta estrutura na raiz do projeto:

```text
.Codex/
  skills/
    spec-driven-development/
      SKILL.md
    python-service-pattern/
      SKILL.md
    fastapi-endpoint/
      SKILL.md
    docker-compose-fix/
      SKILL.md
    security-review/
      SKILL.md
    test-strategy/
      SKILL.md
    release-v1/
      SKILL.md

  agents/
    spec-architect.md
    backend-implementer.md
    devops-reviewer.md
    security-reviewer.md
    code-reviewer.md
    test-engineer.md

specs/
  README.md

AGENTS.md
```

---

# 3. Regras obrigatórias

1. Não implementar código sem SPEC.
2. Toda SPEC deve listar as skills obrigatórias.
3. Toda SPEC deve listar os sub-agents recomendados.
4. Todo sub-agent deve ter responsabilidade clara.
5. Skills devem ser padrões reutilizáveis, não tarefas específicas.
6. Sub-agents devem executar papéis especializados.
7. AGENTS.md deve conter o fluxo oficial do projeto.
8. O projeto deve priorizar simplicidade, segurança, testes e entregas pequenas.
9. Não criar complexidade desnecessária.
10. Antes de editar arquivos importantes, mostrar plano.

---

# 4. Como usar esta skill

## Uso direto

```text
/spec-driven-project-setup
```

## Uso com instrução

```text
Use a skill spec-driven-project-setup para configurar este projeto com skills, specs e sub-agents.
Antes de alterar qualquer arquivo, mostre o plano.
```

---

# 5. Prompt mestre para configurar o projeto

Use este prompt dentro do Codex:

```text
Quero estruturar este projeto usando Codex com:

1. Skills reutilizáveis
2. Specs referenciando skills
3. Sub-agents especializados
4. Fluxo Spec Driven Development

Antes de alterar qualquer arquivo, faça um plano.

Objetivo:

Criar uma estrutura padrão para que qualquer nova feature siga este fluxo:

Ideia → SPEC.md → ARCHITECTURE.md → BACKLOG.md → implementação → testes → revisão → entrega V1.

Crie ou atualize a seguinte estrutura:

.Codex/
  skills/
    spec-driven-development/
      SKILL.md
    python-service-pattern/
      SKILL.md
    fastapi-endpoint/
      SKILL.md
    docker-compose-fix/
      SKILL.md
    security-review/
      SKILL.md
    test-strategy/
      SKILL.md
    release-v1/
      SKILL.md

  agents/
    spec-architect.md
    backend-implementer.md
    devops-reviewer.md
    security-reviewer.md
    code-reviewer.md
    test-engineer.md

specs/
  README.md

AGENTS.md

Regras obrigatórias:

1. Não implementar código sem SPEC.
2. Toda SPEC deve listar as skills obrigatórias.
3. Toda SPEC deve listar os sub-agents recomendados.
4. Todo sub-agent deve ter responsabilidade clara.
5. Skills devem ser padrões reutilizáveis, não tarefas específicas.
6. Sub-agents devem executar papéis especializados.
7. AGENTS.md deve conter o fluxo oficial do projeto.
8. O projeto deve priorizar simplicidade, segurança, testes e entregas pequenas.
9. Não criar complexidade desnecessária.
10. Antes de editar arquivos, mostre o plano e aguarde minha aprovação.

Crie os arquivos com conteúdo real, não placeholders vazios.

Ao final, entregue:

1. Lista de arquivos criados
2. Explicação curta de como usar
3. Exemplo de prompt para criar uma nova feature usando esse fluxo
4. Exemplo de prompt para implementar uma tarefa do BACKLOG
5. Exemplo de prompt para revisar a entrega
```

---

# 6. Conteúdo esperado do AGENTS.md

O `AGENTS.md` deve conter:

- Regras gerais do projeto
- Fluxo obrigatório de trabalho
- Como usar specs
- Como usar skills
- Como usar sub-agents
- Critérios mínimos antes de implementar
- Critérios mínimos antes de finalizar uma tarefa

Modelo:

```md
# Regras do Projeto

Este projeto usa Spec Driven Development.

## Fluxo obrigatório

1. Ideia vira SPEC.
2. SPEC vira ARCHITECTURE.
3. ARCHITECTURE vira BACKLOG.
4. BACKLOG vira implementação.
5. Implementação passa por testes.
6. Implementação passa por revisão.
7. Entrega vira V1.

## Regras

- Não implementar código novo sem SPEC.
- Não alterar escopo sem atualizar a SPEC.
- Não criar solução grande se uma solução simples resolver.
- Não expor secrets em código ou logs.
- Implementar uma tarefa pequena por vez.

## Skills padrão

- spec-driven-development
- python-service-pattern
- security-review
- test-strategy
- release-v1

## Sub-agents padrão

- spec-architect
- backend-implementer
- devops-reviewer
- security-reviewer
- code-reviewer
- test-engineer
```

---

# 7. Conteúdo esperado do specs/README.md

O `specs/README.md` deve explicar:

- Como criar uma nova spec
- Estrutura recomendada de uma pasta de feature
- Exemplo de referência a skills
- Exemplo de referência a sub-agents

Modelo de estrutura:

```text
specs/
  nome-da-feature/
    SPEC.md
    ARCHITECTURE.md
    BACKLOG.md
```

---

# 8. Modelo de SPEC.md

```md
# SPEC — Nome da Feature

## Problema

Descreva o problema real.

## Objetivo

Descreva o resultado esperado.

## Escopo

O que entra.

## Fora de escopo

O que não entra.

## Skills obrigatórias

- spec-driven-development
- python-service-pattern
- security-review
- test-strategy

## Sub-agents recomendados

- spec-architect
- backend-implementer
- test-engineer
- security-reviewer
- code-reviewer

## Requisitos funcionais

1. ...
2. ...
3. ...

## Requisitos não funcionais

1. ...
2. ...
3. ...

## Inputs

- ...

## Outputs

- ...

## Regras de negócio

- ...

## Critérios de aceite

- ...

## Riscos

- ...

## Plano de validação

- ...
```

---

# 9. Modelo de ARCHITECTURE.md

```md
# ARCHITECTURE — Nome da Feature

## Visão geral

Explique a solução em alto nível.

## Componentes

- Componente 1
- Componente 2
- Componente 3

## Fluxo

```text
Entrada → Processamento → Saída
```

## Decisões técnicas

- Decisão 1
- Decisão 2

## Alternativas consideradas

- Alternativa 1
- Alternativa 2

## Riscos técnicos

- Risco 1
- Risco 2

## Observabilidade

- Logs
- Métricas
- Healthcheck
```

---

# 10. Modelo de BACKLOG.md

```md
# BACKLOG — Nome da Feature

## Tarefas

### 1. Criar estrutura base

Status: pendente

Critérios:
- Pastas criadas
- Arquivos base criados

### 2. Implementar lógica principal

Status: pendente

Critérios:
- Código implementado
- Erros tratados
- Logs criados

### 3. Criar testes

Status: pendente

Critérios:
- Testes unitários
- Testes de integração, se necessário

### 4. Revisar segurança

Status: pendente

Critérios:
- Sem secrets expostos
- Sem logs sensíveis
- Inputs validados

### 5. Preparar V1

Status: pendente

Critérios:
- Documentação
- Como rodar local
- Pendências conhecidas
```

---

# 11. Sub-agents recomendados

## spec-architect

Responsável por:

- Criar SPEC.md
- Criar ARCHITECTURE.md
- Criar BACKLOG.md
- Identificar skills obrigatórias
- Identificar sub-agents recomendados
- Não implementar código

## backend-implementer

Responsável por:

- Implementar backend seguindo SPEC
- Ler SPEC, ARCHITECTURE e BACKLOG antes de alterar arquivos
- Implementar uma tarefa pequena por vez
- Rodar testes quando existirem

## devops-reviewer

Responsável por:

- Docker
- Docker Compose
- CI/CD
- Variáveis de ambiente
- Healthcheck
- Execução local

## security-reviewer

Responsável por:

- Secrets
- Logs sensíveis
- Permissões
- Inputs
- Dependências
- Riscos

## code-reviewer

Responsável por:

- Aderência à SPEC
- Simplicidade
- Qualidade do código
- Risco de quebrar produção

## test-engineer

Responsável por:

- Testes unitários
- Testes de integração
- Estratégia mínima de validação
- Cobertura dos critérios de aceite

---

# 12. Prompt para criar uma nova feature

```text
Quero criar uma nova feature chamada healthcheck-api.

Use o fluxo do AGENTS.md.

Antes de implementar código:

1. Crie specs/healthcheck-api/SPEC.md
2. Crie specs/healthcheck-api/ARCHITECTURE.md
3. Crie specs/healthcheck-api/BACKLOG.md
4. Liste as skills obrigatórias
5. Liste os sub-agents recomendados
6. Defina critérios de aceite
7. Não implemente código ainda

Use o sub-agent spec-architect.
```

---

# 13. Prompt para implementar uma tarefa

```text
Leia:

- specs/healthcheck-api/SPEC.md
- specs/healthcheck-api/ARCHITECTURE.md
- specs/healthcheck-api/BACKLOG.md

Implemente apenas a primeira tarefa pendente do BACKLOG.

Regras:

1. Use o sub-agent backend-implementer.
2. Use as skills listadas na SPEC.
3. Altere somente os arquivos necessários.
4. Não implemente tarefas futuras.
5. Rode testes se existirem.
6. Ao final, mostre:
   - arquivos alterados
   - o que foi feito
   - como testar
   - pendências
```

---

# 14. Prompt para revisar uma entrega

```text
Revise a implementação atual contra:

- specs/healthcheck-api/SPEC.md
- specs/healthcheck-api/ARCHITECTURE.md
- specs/healthcheck-api/BACKLOG.md

Use os sub-agents:

- code-reviewer
- security-reviewer
- test-engineer

Verifique:

1. Aderência à SPEC
2. Segurança
3. Simplicidade
4. Testes
5. Logs
6. Tratamento de erro
7. Risco de quebrar produção

Não edite arquivos.
Entregue apenas o relatório de revisão.
```

---

# 15. Prompt para preparar V1

```text
Prepare a entrega V1 da feature healthcheck-api.

Leia:

- AGENTS.md
- specs/healthcheck-api/SPEC.md
- specs/healthcheck-api/ARCHITECTURE.md
- specs/healthcheck-api/BACKLOG.md

Use a skill release-v1.

Verifique:

1. Se todos os critérios de aceite foram atendidos.
2. Se existem testes mínimos.
3. Se existe documentação de como rodar.
4. Se não há secrets expostos.
5. Se o BACKLOG está atualizado.
6. Se existem pendências conhecidas.

Não edite arquivos sem mostrar plano antes.
```

---

# 16. Checklist final

Antes de considerar uma feature pronta:

- [ ] Existe SPEC.md
- [ ] Existe ARCHITECTURE.md
- [ ] Existe BACKLOG.md
- [ ] Skills obrigatórias foram listadas
- [ ] Sub-agents recomendados foram listados
- [ ] Critérios de aceite foram atendidos
- [ ] Testes mínimos foram criados ou justificados
- [ ] Segurança revisada
- [ ] Sem secrets expostos
- [ ] Documentação de uso criada
- [ ] Pendências conhecidas registradas

---

# 17. Regra final

Se uma instrução pedir para implementar código sem SPEC, primeiro criar ou solicitar a SPEC.

Se a SPEC estiver incompleta, completar a SPEC antes de implementar.

Se a tarefa for grande, quebrar em tarefas pequenas no BACKLOG.

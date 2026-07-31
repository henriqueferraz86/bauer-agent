# Harness do Bauer — o que está medido

Harness aqui significa a camada que torna a execução autônoma **previsível,
segura e verificável**: quem governa o run, como o contexto é montado, o que
prova que a tarefa terminou, onde o código é alterado e o que fica auditável.

Não é funcionalidade de IA. É o que impede o agente de declarar sucesso sozinho.

## Estado — 2026-07-31

```bash
python -m evals.harness.medir
```

| Capacidade | % | itens | meta |
|---|---|---|---|
| Uso obrigatório do Kernel | 100% | 12/12 | 100% |
| Kernel e ciclo de vida | 100% | 12/12 | 95% |
| Context Builder | 100% | 9/9 | 90% |
| Validação determinística | 100% | 8/8 | 90% |
| Contrato de tarefa | — | 0/0 | informativa |
| **Isolamento** | **75%** | **3/4** | **85%** |
| Controle de progresso | 100% | 9/9 | 85% |
| Observabilidade | 100% | 17/17 | 90% |
| Capacidade do runtime | 100% | 6/6 | 100% |
| Retry, fallback e recovery | 100% | 6/6 | 90% |
| Avaliações de harness | 100% | 23/23 | 85% |
| **média (só o mensurável)** | **98%** | | 90% |

**21 dos 22 indicadores** do [§15 do plano](PLANO_HARNESS_90.md#15-indicadores-para-declarar-90).

### O que esse número mede — e o que não mede

Ele mede que o encanamento **existe e está no caminho de execução**. É medição
estática: o gate está montado, o evento é emitido, o caminho tem custódia.

Ele **não** mede que o agente produz bom software. Repare na linha em branco:

```
Contrato de tarefa    0/0    sem run autônomo registrado ainda
```

O indicador que responderia *"isso funciona quando o agente trabalha sozinho?"*
está vazio. Toda a maquinaria de validação é uma hipótese bem construída e
ainda não confrontada com um run autônomo real de ponta a ponta.

## Documentos

| Doc | O que tem |
|---|---|
| [PLANO_HARNESS_90.md](PLANO_HARNESS_90.md) | O plano (S7–S14), o inventário do que já existia, o scorecard medido e o backlog |
| [EXECUTION_PATHS.md](EXECUTION_PATHS.md) | Inventário medido dos caminhos de execução e de quem tem custódia |
| [FAILURE_MODES.md](FAILURE_MODES.md) | Modos de falha do harness, cada um com evidência |

## As três lições que valem mais que o número

**1. Custódia ≠ governança.** `execute()`/`stream()`/`continue_run()` rodam os
gates e declaram o desfecho. `admit()` faz preflight e devolve o run ao caller —
o Evaluator **nunca roda**. Dois runs no mesmo store, mesma config,
`evaluator_enabled: true` nos dois:

```
run-71bd5a17  created → planning → policy_check → queued → running → completed
                                                            ^ evaluating AUSENTE
run-7083ac82  created → planning → policy_check → queued → running → evaluating → completed
```

**2. "Existe" não é "está em uso".** Quatro capacidades ficaram travadas em
números baixos porque o componente estava construído e não ligado — o campo
`requires_approval` declarado e nunca lido; o evento `tool.denied` escrito no
store errado; o `GGUFParser` importado e nunca chamado (esse no app que o
próprio Bauer construiu). Medir chamada, não declaração.

**3. Quando o número não sobe, suspeite da régua.** Quatro vezes nesta campanha
o defeito estava em `medir.py`, não na implementação: condição que só aceitava
marca terminada em `.py`; `_grep` devolvendo caminhos e sendo lido como
conteúdo; denominador que zerava justamente quando a capacidade era satisfeita.
Quatro de quatro. **Os indicadores que já nasceram verdes nunca foram
auditados** — o 98% tem essa margem embutida.

## O que falta

**Isolamento — 75%, abaixo da meta de 85%.** Só o container (nível 2) fecha.
O worktree protege o **histórico do git**, não a máquina: o `ShellRunner` é
allowlist por binário, não por caminho, e `python` precisa estar liberado para o
agente rodar testes. Medido: `python -c "open('/tmp/x','w')"` escreve fora do
workspace, `curl` sai para a rede. Alternativa mais barata que fecha a maior
parte: `bwrap`/`systemd-run` em volta do `run_command`.

**P0 ainda aberto:**

| ID | Item | Situação |
|---|---|---|
| HARNESS-008 | Planner persistir aceite | `autonomous_planner` não conhece `acceptance_criteria` |
| HARNESS-010 | `requested_context: auto` | default fixo de 8192 no `config_loader` |
| HARNESS-017 | `workspace_snapshot` / rollback | não existe |

**E o teste que vale mais que os três:** refazer um projeto real do zero, em
modo autônomo, com o harness ligado, e contar quantos defeitos conhecidos são
barrados. Enquanto isso não acontece, o 98% é prontidão arquitetural — não
prontidão de campo.

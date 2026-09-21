# Maturidade do Bauer

O diagnóstico atual marca **9,2/10 — maturidade alta, produção controlada**.
O número é produzido por `bauer maturity --json` e separa evidência de teste,
governança e arquitetura de um simples percentual de cobertura.

| Dimensão | Score | Evidência principal |
|---|---:|---|
| Qualidade estática | 10,0 | Ruff informativo com 0 ocorrências |
| Testes reprodutíveis | 9,0 | Suíte hermética e execução serial/paralela verde |
| Governança Kernel | 9,0 | Entrada governada e teste de custódia |
| Memória unificada | 9,0 | `UnifiedMemory` implementa `MemoryProvider` |
| Modularidade | 9,0 | Fachadas de memória e roteamento isoladas |

Para reproduzir:

```bash
uv run bauer maturity --json
```

O diagnóstico não executa providers reais nem modifica o workspace. A próxima
melhoria de nível deve medir soak tests na Beelink e reduzir gradualmente os
módulos históricos grandes, sem sacrificar as interfaces de compatibilidade.

# SPEC — Sprint 82: "parou" não é "cumpriu", gates fora de git e pytest na imagem

## Pedido

2026-11-12: "instale pytest nos containers. resolva tudo" — os itens que a
sprint 81 deixou abertos.

## Causas

| Problema | Causa |
|---|---|
| /loop que encerra dizendo "nenhuma tarefa foi concluída" sai `completed` com ✅ | `completed` era só "o modelo parou sozinho" (duas rodadas sem tool). Nada perguntava se o objetivo foi cumprido. |
| Gate `tests` sempre "nenhuma mudança — nada a testar" fora de repo git | O fallback lê `result["tools_usadas"]`, e nenhum executor preenchia esse campo. |
| Gate `tests` "stack não detectada" | O workspace do canal é a pasta de trabalho do agente; o projeto fica numa subpasta. |
| pytest do projeto nem inicia no container | Sem config própria, o pytest sobe até `/app/pyproject.toml` (do Bauer) e herda `-n`. |
| `No module named pytest` depois de rebuild | pytest só existia na camada gravável do container. |
| `.venv` do projeto inútil no container | É o venv do host; o `python` dele é symlink quebrado ali. |

## Comportamento

- O nudge de confirmação do loop pede uma linha final `RESULTADO: CONCLUIDO`
  ou `RESULTADO: PENDENTE`. `PENDENTE` vira `stop_reason=objetivo_pendente`:
  run `failed` com erro "o agente encerrou sem cumprir o objetivo", e o
  Telegram mostra "⚠️ /loop encerrou sem cumprir o objetivo". Sem a linha,
  vale o comportamento antigo. A linha é removida do texto entregue.
- Os loops do Telegram e da web devolvem `tools_usadas`; o gate `tests`
  passa a disparar fora de repo git quando houve tool mutante.
- `verify_project` usa a única subpasta com marker de stack quando a raiz não
  tem nenhum. Com zero ou várias candidatas, não adivinha.
- O passo de teste Python isola o pytest (`--rootdir . -c <devnull>`) quando o
  projeto não tem config e um diretório pai tem.
- `.venv-*`/`.venv312` etc. entram como candidatos de interpretador.
- A imagem Docker traz `pytest`.

## Efeito colateral esperado

Em workspace sem git, um /loop que usou tool mutante agora roda a suíte do
projeto ao final (modo regressão: só falha NOVA reprova; timeout de 600 s).

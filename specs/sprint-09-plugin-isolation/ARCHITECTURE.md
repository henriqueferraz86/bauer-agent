# Arquitetura — Sprint 09

## Visão geral

O processo principal mantém o registry, aplica a política e controla o ciclo
de vida. Cada plugin gerenciado roda em um subprocesso dedicado. A comunicação
usa stdin/stdout em JSONL; stderr é tratado como diagnóstico e nunca como
protocolo.

```text
HookRegistry
    ↓ evento normalizado
PluginBroker → policy(manifest) → PluginProcess
                              ← resposta JSONL limitada
```

## Componentes

- `bauer/plugin_process.py`: cliente do processo filho, framing JSONL,
  timeout, limite de bytes e encerramento.
- `bauer/plugin_worker.py`: entrypoint mínimo do filho; importa apenas o
  entrypoint validado e responde a `hello`, `health` e `event`.
- `bauer/plugin_broker.py`: mantém processos, aplica capabilities/permissões,
  faz restart limitado e reduz falhas a estado operacional.
- `bauer/plugin_hooks.py`: adapta os eventos legados para o broker quando o
  plugin gerenciado estiver habilitado.
- `bauer/plugin_manager.py`: expõe o modo de execução e impede ativação de
  manifesto incompatível.

## Contrato IPC

Cada mensagem é um objeto JSON com `protocol: 1`, `request_id`, `kind` e
payload serializável. O filho nunca recebe objetos Python, sessão, token ou
cliente. O broker aceita apenas respostas com `request_id`, `ok` e erro curto;
qualquer outro formato encerra o processo.

Tipos iniciais:

- `hello`: identifica plugin e versão do protocolo.
- `health`: confirma que o loop do filho está responsivo.
- `event`: envia nome do evento e payload sanitizado.
- `shutdown`: encerra de forma cooperativa.

## Política e capacidades

O broker calcula a interseção entre evento, capability e permissões do
manifesto. Eventos não autorizados são descartados antes da escrita no stdin.
O payload passa por uma normalização que remove prompt, resposta, tokens,
headers e objetos não JSON.

## Ciclo de vida

```text
stopped → starting → ready → unhealthy → stopped
                         ↘ restarting ↗
```

Um timeout encerra o processo, marca a chamada como falha e permite no máximo
um restart dentro da janela configurada. O broker usa lock por plugin para
impedir processos duplicados e shutdown idempotente.

## Limites de segurança

Processo separado fornece contenção de crash, bloqueio e timeout. Ele não é
uma sandbox de kernel: o plugin ainda pode acessar recursos do sistema que o
usuário do processo permite. O desenho não deve anunciar isolamento forte
antes de existir um backend OS/container específico.

## Observabilidade

Registrar apenas plugin id, versão, estado, tipo de evento, duração, motivo de
falha e contador de reinícios. Nunca registrar payload de evento, prompt,
resposta, caminho de segredo ou conteúdo retornado pelo plugin.

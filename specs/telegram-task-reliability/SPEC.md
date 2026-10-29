# SPEC — confiabilidade de tarefas iniciadas pelo Telegram

## Problema observado

Em 2026-10-28, a sessão Telegram do projeto Investimentos OS recebeu “Use um
time de agente e inicie a fase 11.” O modelo respondeu com um pseudo tool call
JSON no corpo textual. O executor nativo aceitou o texto como resposta final;
o Kernel marcou a execução `completed` com zero tools. O transporte ainda pode
silenciar uma resposta vazia. O pedido de time também não possui roteamento
automático na entrada Telegram.

## Objetivo

Não declarar sucesso quando o modelo emite uma chamada de tool em texto;
recuperar chamadas válidas e autorizadas pelo conjunto de tools disponível,
reportar falhas em vez de silêncio e encaminhar pedidos explícitos de time pelo
runtime de equipes governado, mantendo workspace/projeto e identidade Telegram.

## Fora de escopo

Executar a Fase 11 do Investimentos OS, alterar seus arquivos ou mudar
credenciais/configuração de modelos.

## Aceite

- Pseudo tool call textual válido é normalizado e executado pela rota nativa,
  sob as mesmas políticas, orçamento, timeout e registro de tools.
- JSON desconhecido/malformado não é executado nem apresentado como sucesso.
- Uma mensagem inbound válida sempre recebe resposta final ou aviso explícito.
- Pedido explícito para usar time não cai no agente individual sem indicação.
- Execução da equipe mantém Kernel, projeto/workspace, session id e identidade.
- Falhas e conclusão são observáveis sem registrar tokens ou credenciais.

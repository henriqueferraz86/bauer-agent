# Sprint 24 — Sincronização automática de projetos

## Objetivo

Fazer a aba **Projetos** detectar automaticamente as pastas criadas pelo Bauer
sem exigir recarregar a página manualmente.

## Escopo

- Atualizar a lista de projetos periodicamente enquanto a tela estiver aberta.
- Preservar o projeto selecionado pelo usuário durante a atualização.
- Manter a sincronização no endpoint do Server como fonte de verdade.
- Cancelar o temporizador ao sair da tela.

## Fora de escopo

- Monitoramento nativo do sistema de arquivos no backend.
- Descoberta de pastas fora do workspace configurado.
- Alteração do registro de projetos ou exclusão de pastas.

## Critérios de aceite

1. Uma pasta criada no workspace aparece na aba Projetos sem `Ctrl+F5`.
2. A seleção atual não volta para o projeto ativo a cada atualização.
3. O polling é interrompido quando a tela é desmontada.
4. Falhas temporárias de rede continuam sendo exibidas sem derrubar a tela.
5. O build TypeScript passa.

## Validação

- `npm run test -- --run`
- `npm run build`

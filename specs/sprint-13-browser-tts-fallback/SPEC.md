# SPEC — fallback de voz no chat web

## Objetivo

Garantir que uma mensagem de voz transcrita receba resposta audível no navegador mesmo quando o endpoint `/speak` não tiver provider TTS configurado no servidor.

## Escopo

- Manter `/speak` como primeira opção para voz de melhor qualidade.
- Usar `speechSynthesis` do navegador quando `/speak` falhar ou não estiver disponível.
- Informar no estado de voz quando a resposta foi encaminhada por fallback ou quando nenhum recurso de voz existe.
- Cobrir o comportamento com testes unitários do frontend.

## Fora de escopo

- Instalar pesos ou providers TTS na Beelink.
- Alterar o contrato do endpoint `/speak`.
- Reprodução automática de áudio em mensagens digitadas.

## Critérios de aceite

1. Um turno de voz tenta `/speak` antes do fallback do navegador.
2. Uma falha HTTP/TTS em `/speak` dispara `speechSynthesis.speak` quando disponível.
3. O fallback seleciona `pt-BR` e não lança erro quando a API do navegador não existe.
4. A suíte do desktop e o build passam.

## Validação

- `npm test -- --run`
- `npm run build`
- CI completo do repositório

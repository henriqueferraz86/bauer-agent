# ARCHITECTURE — fallback de voz no chat web

O fluxo de resposta de voz permanece no `Chat.tsx`: o endpoint `/speak` produz WAV quando um provider do servidor está configurado. Uma função pequena e isolada em `voice.ts` encapsula a Web Speech API, permitindo testar a disponibilidade sem acoplar o componente à API global.

A ordem de execução é:

1. `api.audio("/speak", { text })`.
2. Reprodução do WAV retornado.
3. Em qualquer falha de transporte, síntese ou autoplay, `speakBrowserFallback(text)`.
4. Se o navegador não oferecer `speechSynthesis`, o texto continua na conversa e o estado de voz informa a indisponibilidade.

A fala do navegador usa `pt-BR`, cancela uma fala anterior para evitar sobreposição e escolhe uma voz em português quando o navegador já expôs a lista de vozes.

# Telegram via AgentOS HTTP

O modo `agentos_http` é experimental e permanece desligado. O Kernel governa a run no Gateway; sua admissão não substitui a política de ferramentas em outro processo. Não habilite ferramentas de escrita ou HITL antes das provas descritas em [telegram-agentos-security.md](telegram-agentos-security.md).

## Diagnóstico

1. Registre os commits e versões dos serviços, sem incluir credenciais nos registros. Faça backup protegido de configuração e bancos SQLite antes de qualquer alteração.
2. Consulte `/health`, `/info`, catálogo e `/openapi.json` da instalação efetiva. Confirme autenticação, isolamento, porta restrita e IDs de agentes e times.
3. Confirme os consumidores existentes antes de alterar autenticação ou bind. O endpoint experimental de capacidades exige JWT e um desafio; a assinatura prova posse da chave compartilhada, não a integridade do processo remoto.
4. Rode testes herméticos contra a imagem efetivamente instalada, usando banco e rede isolados. Valide aprovação, cancelamento, recovery, mídia, custos e isolamento antes do canary.

Em 13–14/11/2026 a Beelink estava no commit `675aedd`, com AgentOS 3.2, `auth_mode=none`, `user_isolation=false` e a porta 7777 publicada em todas as interfaces. A configuração efetiva e os serviços devem ser reavaliados antes do rollout. Um backup dos 13 bancos observados passou em `PRAGMA quick_check`; refaça-o imediatamente antes de alterar produção.

## Limites conhecidos

- O caminho experimental aceita texto e documentos `.txt` limitados. Fotos, áudio e outros binários ainda não têm paridade; o scanner de segredos de documentos é heurístico.
- No perfil sem ferramentas externas, o projeto selecionado isola a sessão, mas seu conteúdo e workspace não são enviados ao AgentOS. No perfil integrado de leitura, os resultados das leituras autorizadas são enviados ao AgentOS.
- A persistência da inbox evita repetir um POST remoto incerto. Se a entrega ao Telegram for ambígua, não repita o envio automaticamente: a API não oferece uma chave idempotente para comprovar se a mensagem anterior foi aceita.
- O cancelamento é anunciado como confirmado somente após estado remoto terminal. O registro de runs ativas é vinculado à identidade, sessão e processo; outro worker ou processo reiniciado pode recusar o pedido em vez de afirmar que a execução parou.
- O perfil experimental conecta ferramentas externas de leitura do AgentOS ao Tool Gateway quando explicitamente configurado. A inicialização falha fechada sem Kernel, credenciais independentes, política explícita válida e stores de projetos/sessões existentes. AgentOS e Tool Gateway exigem HTTPS ou loopback; configure terminação TLS confiável sem aceitar cabeçalhos de proxy arbitrários. A retomada controlada e sua proteção contra replay foram testadas em isolamento, mas a aprovação Telegram e as ferramentas de escrita ainda não estão integradas. A migração de produção continua condicionada a todos os gates do plano.

## Rollback

Interrompa novas admissões HTTP, reconcilie as runs ativas e confirme estados terminais ou pausados antes de mudar o adapter somente do Telegram. Valide duas conversas após o rollback. Nunca reenvie um POST ou transfira uma execução em andamento para outro motor quando a admissão remota for incerta.

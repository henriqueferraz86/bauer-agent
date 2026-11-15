# Fronteira de segurança Telegram → AgentOS

O perfil do canal exige identidade JWT e uma instância separada do AgentOS.
A chave de serviço e o bind de rede não substituem a governança das ferramentas.
A configuração atual do AgentOS na
Beelink foi inspecionada sem alteração: informava `auth_mode=none`,
`user_isolation=false` e escutava na porta 7777 em todas as interfaces.

## Limites identificados no Agno instalado localmente

- `POST /agents/{id}/runs` e o equivalente de times aceitam `version` em
  formulário. `resolve_agent`/`resolve_team` podem reconstruir componentes
  persistidos no banco. Remover tools apenas dos objetos criados em
  `bauer/agentos_app.py` não protege componentes reconstruídos ou versões.
- A API também expõe endpoints de mutação e administração, incluindo
  componentes e suas configurações, sessões, memória, agendamentos, contas de
  serviço e migração de banco. Um token que acesse toda a API tem capacidades
  além de executar os objetos de catálogo já materializados.
- `OS_SECURITY_KEY` autentica o portador da chave compartilhada; não prova a
  identidade ou as permissões do usuário do Telegram. `user_id` do formulário
  é dado fornecido pelo cliente e não deve servir como identidade confiável.
- O isolamento de usuário do middleware Agno é opt-in e depende de identidade
  autenticada e configuração de autorização. Mesmo JWT/scopes não provam, por
  si, que a aprovação Bauer cobre cada chamada de ferramenta executada em
  outro processo.
- O middleware do Agno usado no lock local importa `jwt` ao receber chamadas;
  `PyJWT` precisa estar presente tanto no ambiente instalado quanto na imagem.
  `/health` permanece acessível sem credencial no teste local, enquanto
  `/config` e a rota Bauer de seleção de modelo exigem a chave.

A Beelink informava AgentOS 3.2.0 durante o diagnóstico; o lock local usava
3.0.10. Repita a inspeção e os testes no artefato implantado: rotas e resolução
de componentes podem variar entre versões.

## Contenção opcional para testes locais

`BAUER_AGENTOS_CHANNEL_BOUNDARY=true` está desligado por padrão e destina-se
somente a testes locais. **Não habilitar em produção nem com múltiplos
usuários.** Exige `OS_SECURITY_KEY`, remove tools
dos agentes e membros materializados e limita a API a `/health`, `/info` e às
operações de criação de runs para IDs
materializados. Nega outras rotas, WebSockets, upload de arquivos, seleção de
versão, factories, parâmetros extras e formulários maiores que 256 KiB. Também
nega consultas, cancelamentos e continuações quando usa chave compartilhada:
ownership e HITL remoto não foram comprovados nesse perfil. Ele não faz
isolamento por usuário, nem protege contra outros processos que alterem o banco,
nem garante que as regras internas de resolução sejam iguais em outra versão
do Agno. Não representa autorização para implantação ou canary.
Em particular, o POST aceita `user_id` e `session_id` do portador da chave;
eles não são verificados como identidade ou propriedade da sessão.

O modo `BAUER_AGENTOS_JWT_ISOLATION=true` exige a fronteira de canal acima e habilita RBAC e
isolamento do Agno, exige uma chave HS256 aleatória de pelo menos 32 bytes,
`BAUER_AGENTOS_JWT_AUDIENCE` e `BAUER_AGENTOS_JWT_ISSUER`, e recusa fontes
alternativas de autenticação. Os JWTs precisam conter `sub`, `exp`, `iss`,
`aud` e lista de scopes; tokens administrativos são recusados. Esses testes
são locais e não comprovam a versão implantada ou governança de tools. Nesse
perfil, consulta e pedido de cancelamento exigem `session_id` único e passam
pela verificação de ownership do Agno; testes locais cobrem acesso próprio e
cruzado para agentes e times. Pedido aceito não significa cancelamento terminal.
O emissor do Telegram deriva o sujeito do usuário autenticado. A continuação
genérica permanece bloqueada. Com `BAUER_AGENTOS_EXTERNAL_READ_TOOLS` e as
chaves independentes de atestação e retomada, o perfil expõe uma continuação
dedicada a resultados autenticados de `read_file`. O Tool Gateway valida
projeto, run, chamada e política antes de executar o built-in. A escrita
e o callback de aprovação seguem os gates do [plano de ativação](../../specs/telegram-agentos-http/PLANO_ATIVACAO_TOOL_GATEWAY.md).

## Condições para uma fronteira comprovável

1. Autenticar o Gateway perante o serviço com credencial protegida, rede
   restrita e, para cada usuário, uma identidade verificável e vinculada pelo
   servidor. Validar escopo de projeto, conversa e operação sem confiar no
   payload ou no modelo. Uma credencial compartilhada de serviço não basta.
2. Ter uma superfície de execução que aceite apenas os agentes/times
   explicitamente autorizados e as versões verificadas no servidor. Bloquear
   componentes de banco, factories e parâmetros de versão arbitrários, ou
   validar recursivamente cada componente reidratado e todas as tools antes da
   execução. Restringir a API de administração separadamente, incluindo
   componentes, contas de serviço, filas, sessões, memória e agendamentos.
3. Aplicar policy no executor para **cada** tool e escopo de workspace, ou
   encaminhar chamadas a um Tool Gateway autenticado e auditável. Uma escrita
   só pode ocorrer uma vez após aprovação explícita, relacionada ao usuário,
   execução e `tool_call_id`; identidade ou argumentos fornecidos pelo modelo
   não são confiáveis. Negar ferramentas sem política ou sem conectividade de
   aprovação; falha de auditoria obrigatória também deve negar a operação.
4. Persistir a relação entre runs e ownership, e impedir que outro usuário
   consulte, continue, aprove ou cancele a execução. Tratar retomada,
   cancelamento confirmado e reinício sem repetir efeitos.
5. Testar com a versão e imagem reais: componente persistido com tool de
   escrita, versão explícita, factory e time com membro com tool; mutações em
   cada rota administrativa; usuário A tentando acessar ou aprovar execução de
   B; tool proibida; aprovação/negação, uma única execução e recuperação após
   reinício. Nenhum desses casos deve ser considerado coberto por um teste
   isolado da chave de serviço.

O bind padrão do Compose é `127.0.0.1`; `AGENT_OS_BIND_ADDRESS` pode alterá-lo.
Se a UI ou outro cliente for remoto, use uma fronteira autenticada e restrita,
não abra a porta publicamente. Antes de ativar autenticação, inventarie os
clientes existentes e valide o healthcheck para evitar interrupção inadvertida.

# Sprint 21 — Autenticação do frontend do `bauer serve`

## Problema

O frontend do `bauer serve` exige que o operador copie `BAUER_SERVE_API_KEY`
para a tela de configuração. A SPA grava a chave bruta em `localStorage` e a
envia como `X-API-Key` em todas as requisições. Além de criar atrito, isso
expõe uma credencial administrativa de longa duração a qualquer JavaScript que
execute na mesma origem.

## Objetivo

Adicionar um fluxo de identidade para o único administrador da instância:

1. cadastro realizado uma única vez;
2. login posterior com e-mail e senha;
3. login alternativo com Google;
4. sessão persistente no navegador;
5. autorização automática das chamadas da SPA sem revelar a API key ao
   JavaScript.

`X-API-Key` continua existindo para CLI, automações e integrações externas. A
SPA deixa de armazená-la depois da migração para sessão.

## Escopo

- bootstrap de um único administrador, protegido pela API key atual;
- cadastro local com e-mail e senha;
- cadastro/login com Google Identity Services, quando configurado;
- hash de senha com Argon2id;
- sessões opacas persistidas em SQLite e cookie `HttpOnly`;
- proteção CSRF para requisições autenticadas por cookie;
- rate limit específico para setup e login;
- telas de setup, login e logout na SPA;
- migração automática: remover `bauer.apiKey` do `localStorage` após login;
- recuperação/troca de senha com a API key de bootstrap;
- documentação de configuração local, Docker e Google.
- autenticação experimental do provider OpenAI/ChatGPT pelo browser em
  **Settings**, reutilizando o fluxo OAuth já existente no Bauer;
- callback local com PKCE e `state`, sem entregar tokens à SPA;
- persistência criptografada da credencial OpenAI no volume do Docker.
- após login OpenAI concluído, selecionar automaticamente o provider `openai` e
  o modelo `gpt-5.6-luna`, sem exigir uma segunda ação no menu de modelos;
- usar `reasoning.effort=high` no backend ChatGPT para o Luna, priorizando a
  qualidade das respostas;
- permitir selecionar globalmente, no Server, entre o executor `bauer_native`
  e o adapter `agno`, aplicando a escolha aos próximos turnos;
- executar as tools Bauer também quando o adapter Agno usar a sessão ChatGPT
  OAuth do browser, traduzindo chamadas de função para a Responses API e
  devolvendo os resultados ao ciclo de execução do Agno;

## Fora de escopo

- múltiplos usuários, convites, equipes ou RBAC;
- recuperação por e-mail;
- MFA/TOTP/passkeys;
- usar tokens Google para acessar APIs Google;
- transformar a API key existente em token de usuário;
- armazenar senha, ID token Google ou sessão em `localStorage`;
- remover a autenticação por `X-API-Key` das integrações existentes.
- declarar o OAuth de ChatGPT como mecanismo oficial da API pública OpenAI;
- suportar callback OAuth em um servidor remoto diferente da máquina onde o
  navegador está aberto.

## Skills obrigatórias

- spec-driven-project-setup
- python-service-pattern
- fastapi-endpoint
- security-review
- test-strategy
- openai-docs

## Sub-agents recomendados

- spec-architect: contrato e decisões;
- backend-implementer: store, endpoints e integração com o serve;
- security-reviewer: segredo, sessão, CSRF, takeover e OAuth;
- test-engineer: unitários, integração e frontend;
- code-reviewer: aderência à SPEC e compatibilidade.

## Requisitos funcionais

1. `GET /auth/state` informa se auth está habilitada, se setup é necessário,
   se a requisição tem sessão válida e se Google está configurado.
2. Quando não existe administrador, `POST /auth/setup` exige a API key atual,
   cria exatamente um administrador e inicia uma sessão.
3. Tentativas simultâneas de setup não podem criar dois administradores.
4. `POST /auth/login` aceita e-mail/senha e devolve erro genérico para falhas.
5. `POST /auth/google` valida um Google ID token no backend. No primeiro uso
   também exige a API key atual; depois só aceita a identidade Google já
   vinculada ao administrador.
6. `POST /auth/logout` revoga a sessão atual e remove os cookies.
7. `POST /auth/recover` exige a API key atual e troca/define a senha do único
   administrador sem criar outro usuário.
8. Uma sessão válida autoriza as mesmas rotas hoje protegidas por
   `Depends(_verify_key)`.
9. A API key válida continua autorizando essas rotas sem cookie.
10. A SPA bloqueia as telas internas até concluir setup ou login.
11. Após autenticar, a SPA remove a chave legada de `localStorage`.
12. Settings mostra o estado da autenticação OpenAI e permite iniciar ou
    remover a credencial pelo browser.
13. `POST /api/auth/openai/start` inicia uma única transação PKCE e devolve
    somente a URL de autorização; nunca devolve token ou verifier.
14. O callback em `localhost:1455/auth/callback` valida `state`, troca o código
    no backend e persiste a credencial criptografada.
15. `GET /api/auth/openai/status` informa apenas estado, tipo, expiração e
    disponibilidade; `POST /api/auth/openai/logout` remove a credencial.
16. A conclusão do OAuth OpenAI aplica a seleção runtime `openai/gpt-5.6-luna`;
    se a conta não aceitar o modelo, a autenticação permanece válida e o
    endpoint retorna um aviso sanitizado para o frontend.
17. Chamadas ChatGPT Responses para `gpt-5.6-luna` enviam explicitamente
    `reasoning.effort=high`; chamadas de outros modelos preservam o esforço
    configurado/default existente.
18. A tela Runtime permite selecionar globalmente `Bauer nativo` ou `Agno`;
    a escolha é persistida no volume do Server e os runs seguintes registram o
    adapter selecionado.
19. A superfície AgentOS expõe o catálogo de modelos do Bauer e permite trocar
    o modelo global dos agentes/times; OpenAI OAuth usa a sessão do browser
    salva pelo Bauer, sem exigir `OPENAI_API_KEY`.
20. Com `runtime_mode=agno` e provider `openai` autenticado por OAuth, o
    modelo recebe as tools Bauer permitidas, chamadas de função são executadas
    pelo `ToolRouter` com as mesmas políticas do Server e o resultado é
    reenviado ao modelo até uma resposta final.

## Requisitos não funcionais

- senha nunca é registrada ou armazenada em texto puro;
- senha usa Argon2id com parâmetros ao menos equivalentes ao mínimo OWASP;
- tokens de sessão têm no mínimo 256 bits de entropia e só o hash é persistido;
- cookie de sessão usa `HttpOnly`, `SameSite=Lax`, `Path=/` e `Secure` em modo
  `reverse_proxy`/HTTPS;
- autenticação por cookie exige token CSRF em operações mutáveis e no stream
  GET que inicia trabalho;
- mensagens de login não revelam se o e-mail existe;
- setup/login/recovery têm limitador próprio, independente do rate limit geral;
- nenhum segredo aparece em URL, logs, eventos, respostas ou bundle frontend;
- falha auxiliar do Google não derruba login local nem o processo do serve;
- banco e cookies mantêm compatibilidade entre Linux e Windows.
- estado/verifier OAuth expiram em até cinco minutos e existem somente em
  memória;
- access token, refresh token, ID token e API key nunca aparecem em resposta,
  URL da aplicação, log ou `localStorage`;
- o callback aceita somente `localhost:1455`, usa `state` constante-time e a
  porta fica publicada no Docker somente para esse callback.

## Configuração

- `serve.web_auth_enabled`: habilita o fluxo; padrão `true` quando existe
  `serve.api_key`, inócuo quando a API não exige autenticação;
- `serve.auth_session_hours`: validade da sessão, padrão 168 horas;
- `BAUER_AUTH_GOOGLE_CLIENT_ID`: client ID público do Google Identity Services;
- a API key existente é a credencial de bootstrap e recuperação.

## Regras de negócio

- existe no máximo um administrador (`id=1`);
- o primeiro cadastro precisa provar posse da API key da instância;
- login Google inicial e login local inicial são alternativas;
- uma conta criada pelo Google pode definir senha depois via recuperação;
- login Google posterior exige o mesmo `sub` do Google já vinculado; igualdade
  apenas de e-mail não é suficiente;
- desabilitar web auth não desabilita a proteção existente por API key.

## Critérios de aceite

1. Uma instalação nova exibe setup e rejeita cadastro sem bootstrap válido.
2. Após setup local, refresh mantém a sessão e nenhuma API key fica em
   `localStorage`.
3. Logout revoga o token no banco; reutilizá-lo retorna 401.
4. Login errado, CSRF ausente e Google token inválido são rejeitados.
5. Um segundo setup é rejeitado mesmo sob concorrência.
6. Google cria/vincula o primeiro administrador somente com bootstrap válido.
7. Chamadas existentes com `X-API-Key` continuam passando.
8. Modo sem `serve.api_key` continua funcionando como antes no loopback.
9. Testes de backend e frontend cobrem os fluxos críticos.
10. A documentação ensina setup, Google Client ID, login, recuperação e
    desativação controlada.
11. O botão OpenAI abre o browser, o frontend acompanha o status e exibe sucesso
    sem receber segredo.
12. Callback com state incorreto/expirado é rejeitado e não grava credencial.
13. A credencial sobrevive a `docker compose up --force-recreate`.
14. O fluxo é rotulado como experimental e a UI mantém API key como caminho
    oficial para a OpenAI API.
15. Após autenticar OpenAI, Settings mostra provider/modelo selecionados e uma
    mensagem de aviso caso o Luna não esteja disponível para a conta.
16. Um teste de contrato confirma o corpo ChatGPT enviado com Luna e esforço
    baixo, sem expor tokens.
17. Um teste de contrato confirma a ida e volta de uma chamada de função OAuth,
    incluindo `function_call`, `function_call_output` e execução sob política.

## Riscos

- takeover da instância antes do primeiro cadastro;
- XSS roubar credenciais armazenadas no navegador;
- CSRF em endpoints mutáveis;
- enumeração de usuário e força bruta;
- configuração incorreta do Client ID Google;
- banco de sessão fora de volume persistente no Docker;
- regressão em clientes atuais que usam `X-API-Key`.
- porta 1455 ocupada ou não publicada no Docker;
- confusão entre assinatura ChatGPT e billing da API OpenAI;
- dependência de fluxo OAuth não documentado publicamente pela OpenAI.

## Plano de validação

- testes unitários do store de autenticação e hashing;
- testes FastAPI para setup/login/logout/recovery/CSRF/API key;
- testes de validação do Google com verifier injetado, sem rede;
- testes Vitest do cliente e do gate de autenticação;
- build da SPA e verificação do bundle;
- suíte completa, Ruff crítico e informativo;
- smoke test Docker em Windows nas portas 8000/7777/3000.
- testes do broker OAuth sem rede, endpoints protegidos e popup/polling;
- smoke da imagem com porta 1455 e diretório de tokens persistente.

## Referências de segurança

- Google backend authentication:
  https://developers.google.com/identity/sign-in/web/backend-auth
- Google OpenID Connect:
  https://developers.google.com/identity/openid-connect/openid-connect
- OWASP Password Storage:
  https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP Session Management:
  https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- OpenAI API authentication (o caminho oficial usa API key ou workload
  identity; o OAuth de ChatGPT desta feature é experimental):
  https://developers.openai.com/api/reference/overview#authentication

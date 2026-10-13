# ARCHITECTURE — contexto de projeto do chat

## Backend

Uma resolução compartilhada fornece router e metadados efetivos. Reutilizar
ProjectRouterCache e precedência do servidor. Expor `GET /chat/context` com
`session_id` e `project_id` opcionais, protegido pelo mesmo verify_key.
Consulta não cria sessão, run, prompt nem chamada de provider.

Contrato JSON (também payload do evento SSE `context`):

```json
{
  "session_id": "id ou null antes de iniciar",
  "project": {"id": "id", "name": "nome", "path": "/pasta"},
  "workspace": "/pasta efetiva do router",
  "source": "session | active | explicit | default",
  "active_project": {"id": "id global", "name": "nome", "path": "/pasta"},
  "warning": null
}
```

`project` e `active_project` podem ser null. `warning` explica fallback quando
um projeto foi solicitado mas seu router não pôde ser resolvido. O endpoint e
o evento usam a mesma função de resolução do turno; SSE inclui a sessão gerada
e precede texto/eventos do modelo, inclusive bloqueios de admissão.

Contexto/prompt de cada turno usa seu router/workspace efetivo. Manter compatível
o callback de `_new_context` usado pelos módulos de chat e OpenAI; parâmetros
opcionais são possíveis, sem duplicar loop de execução.
O módulo `/chat` recebe callback opcional de contexto com router de projeto;
consumidores sem resolução por projeto continuam com o callback existente.
O hint antigo baseado no ativo global foi removido para não contradizer uma
sessão vinculada a outro projeto; prompt sempre declara workspace efetivo.

A sessão b1633939 já resolveu investimentosOS, mas executou em modo Agno.
`_run_agno_turn` recebia seu router e descartava a raiz ao construir um adapter
com config global; o adapter criava outro router no workspace padrão. Injetar
ToolRouter opcional em `AgnoRuntimeAdapter` e informar workspace efetivo no
adapter_cfg. Serve usa seu router já governado e conectado ao EventBus; outros
callers mantêm construção existente. Testar o router usado pelas ferramentas
Agno com configuração/provider fake, além dos dois modos de execução HTTP.

## Frontend

Módulo pequeno `chatProjectContext.ts` define tipo, valida payload e consulta
contexto. `chatStream.ts` guarda contexto na vida do turno e trata evento
`context` separado do texto. Tela Chat consulta backend ao montar/mudar sessão
e ao iniciar Nova sessão; polling leve detecta mudanças no ativo global.
Resultados atrasados não podem sobrescrever sessão/contexto de um turno novo.
Estado indisponível é claro; header mostra projeto efetivo, caminho e aviso.
Link para Projetos e Nova sessão são suficientes para escolher outro projeto.

## Riscos e decisões

O projeto ativo é global, mas a sessão é fixa. Não reenviar project_id global
para sobrepor sessões antigas. Metadados do stream prevalecem na execução.
Estado técnico interno de tools continua separado do contexto público.

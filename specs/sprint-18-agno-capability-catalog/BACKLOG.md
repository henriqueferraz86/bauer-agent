# Backlog — Sprint 18: Agno capability catalog

## 1. Tipos e manifesto do catálogo

Status: concluído

- Criar modelos/metadata imutáveis para toolkit, ação, risco, extra, requisito e
  factory.
- Listar tools Bauer atualmente suportadas e toolkits Agno previstos, marcando
  claramente entradas ainda sem factory.
- Fazer discovery sem import de optional extras ou chamadas externas.

## 2. Validar e materializar tools por agente

Status: concluído

- Resolver a lista declarada na AgentSpec pelo registro.
- Rejeitar IDs/ações desconhecidos ou não suportados antes de instanciar Agno.
- Adaptar ferramentas Bauer já suportadas como wrappers tipados do ToolRouter.
- Confirmar que policy contextual existente continua efetiva para a chamada.

## 3. API de catálogo e estado

Status: concluído

- Expor endpoint autenticado read-only com metadata pública e estado calculado.
- Retornar requisitos/dependência ausentes sem nomes/valores de secrets.
- Testar enabled/disabled e estados de capacidade de forma hermética.

## 4. UI de visibilidade

Status: concluído

- Mostrar catálogo/status no painel existente de agentes/capacidades.
- Distinguir disponível, configurado, dependência ausente, não suportado e
  policy bloqueada.
- Não adicionar mutações de configuração na interface desta sprint.

## 5. Segurança, compatibilidade e testes

Status: concluído

- Adicionar testes para não importar packages opcionais ao listar catálogo.
- Provar que tool/action desconhecidas não são ignoradas e não iniciam provider.
- Verificar redaction, escopo e allowlists por agente.
- Testar config antiga, pacote Agno ausente e atualização não destrutiva.
- Rodar suite, Ruff, mypy e build desktop; revisar diff e atualizar este backlog.

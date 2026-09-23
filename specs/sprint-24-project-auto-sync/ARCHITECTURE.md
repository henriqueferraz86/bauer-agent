# Arquitetura — sincronização automática de projetos

O endpoint autenticado `GET /api/projects` continua responsável por executar a
descoberta idempotente (`sync_workspace_projects`) e enriquecer o registro. A
tela `Projects` fará uma consulta inicial e consultas periódicas de 10 segundos.

O estado selecionado será atualizado de forma funcional pelo `id`, evitando que
o polling substitua uma seleção manual pelo projeto ativo global. O intervalo é
limpo no desmontar do componente para não deixar requisições após a navegação.

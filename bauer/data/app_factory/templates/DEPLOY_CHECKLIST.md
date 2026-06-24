# DEPLOY_CHECKLIST.md

## Build

- [ ] Build roda sem erro
- [ ] Dependências instaladas
- [ ] Variáveis de ambiente configuradas

## Runtime

- [ ] Aplicação sobe
- [ ] Healthcheck funciona
- [ ] Logs acessíveis
- [ ] Porta correta configurada

## Banco

- [ ] Banco criado/configurado
- [ ] Migrations aplicadas
- [ ] Backup considerado

## Segurança

- [ ] HTTPS configurado ou planejado
- [ ] Secrets configurados no ambiente
- [ ] CORS revisado
- [ ] Erros de produção não expõem detalhes internos

## Rollback

- [ ] Estratégia de rollback documentada
- [ ] Versão anterior recuperável

## Comandos úteis

```bash
docker compose up -d --build
docker compose logs -f
docker compose down
```

## Status

Aprovado | Reprovado | Pendente

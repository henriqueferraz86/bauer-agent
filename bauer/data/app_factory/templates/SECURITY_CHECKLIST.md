# SECURITY_CHECKLIST.md

## Secrets

- [ ] `.env` real não foi commitado
- [ ] `.env.example` existe
- [ ] Tokens não estão hardcoded
- [ ] Senhas não aparecem em logs
- [ ] Chaves de API não estão no frontend

## Entrada de dados

- [ ] Inputs são validados
- [ ] Tipos são checados
- [ ] Campos obrigatórios são tratados
- [ ] Dados inválidos retornam erro claro

## API

- [ ] Erros não expõem stack trace em produção
- [ ] CORS está restrito
- [ ] Rate limit foi considerado
- [ ] Autenticação existe quando necessário
- [ ] Autorização existe quando necessário

## Banco

- [ ] SQL injection foi considerado
- [ ] Consultas usam ORM, query builder ou parâmetros
- [ ] Credenciais estão em variável de ambiente

## Frontend

- [ ] XSS foi considerado
- [ ] Dados externos são tratados com cuidado
- [ ] Tokens não ficam expostos indevidamente

## Uploads

- [ ] Tipo de arquivo validado
- [ ] Tamanho limitado
- [ ] Nome de arquivo tratado
- [ ] Armazenamento seguro

## Resultado

Status: Aprovado | Reprovado | Aprovado com ressalvas

Riscos restantes:
-

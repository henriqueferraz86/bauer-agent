# Política de Release

## Versionamento (SemVer)

`MAJOR.MINOR.PATCH` — ex: `0.2.1`

| Tipo | Quando incrementar | Exemplos |
|---|---|---|
| **MAJOR** | Quebra de compatibilidade com versões anteriores | Remoção de comando CLI, mudança de schema no `config.yaml`, remoção de campo de API pública |
| **MINOR** | Nova funcionalidade, sem quebra | Novo provider, novo comando, nova tool, nova seção no config com default |
| **PATCH** | Correção de bug, atualização de dependência de segurança | Fix de crash, CVE de dependência, typo em mensagem de erro |

> Enquanto estamos em `0.x.y`, o MAJOR permanece em 0 e MINOR funciona como se fosse MAJOR (mudanças significativas incrementam MINOR). Qualquer feature nova pode ir em MINOR mesmo com mudança de interface.

---

## Checklist de Release

Execute cada item em ordem antes de criar a tag.

### 0. Runtime beta

Antes de um beta do Agent Runtime, confirme:

- `README.md` aponta para o roteiro do beta.
- `docs/ROADMAP.md` descreve o marco atual e proximos passos.
- RFCs relevantes tem status definido.
- `docs/BETA_CLOSED.md` tem demo repetivel em 5 minutos.
- `config.yaml.example` documenta campos novos com defaults compativeis.
- Config antiga sem `runtime.adapters` passa em `bauer config check`.

### 1. Testes e qualidade

```sh
# Suite completa verde
uv run pytest -q

# Lint crítico limpo (bloqueante no CI)
uv run ruff check bauer/ --select E9,F63,F7,F82

# Lint informativo (revisar se há novos erros relevantes)
uv run ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303 || true
```

### 2. Dependências

```sh
# Atualizar lockfile
uv lock

# Verificar vulnerabilidades no desktop
cd desktop && npm audit --audit-level=high
```

### 3. CHANGELOG.md

- Adicionar seção `## [X.Y.Z] — YYYY-MM-DD` acima de `## [0.2.0]`
- Preencher seções `### Adicionado`, `### Corrigido`, `### Segurança`, `### Removido`
- Usar frases no passado, sem artigo inicial ("Adiciona X" → "X adicionado")

### 4. Bump de versão

```sh
# Editar pyproject.toml: version = "X.Y.Z"
# Confirmar que a versão está correta
python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(d['project']['version'])"
```

### 5. Commit e tag

```sh
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): v X.Y.Z"
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin master --tags
```

### 6. Verificação pós-tag

```sh
# CI deve disparar e ficar verde
# Verificar: Actions → workflow "ci" no GitHub

# Confirmar que a tag aparece
git tag --sort=-version:refname | head -3
```

---

## Branches e PRs

- **Fixes pequenos** → direto no `master` (sem branch/PR)
- **Features novas** → branch + PR (revisar antes de merge)
- **Nunca** commitar `config.yaml` com `api_key` preenchida ou credenciais

## Sobre binários (Tauri)

O build de release Tauri (`tauri build`) só é executado via CI — WDAC no Windows bloqueia binários locais não assinados. Para verificar o desktop, usar `npm run dev` dentro de `desktop/` com `bauer serve` rodando localmente.

Assinatura de código (futuro): aguardando decisão sobre certificado — ver [issue pendente].

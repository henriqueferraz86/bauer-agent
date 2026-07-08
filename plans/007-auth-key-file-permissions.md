# Plan 007: Restringir permissões do arquivo `.auth_key` para 0o600 e corrigir docstring enganoso

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 2c9d86f..HEAD -- bauer/auth.py`
> If `bauer/auth.py` changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `2c9d86f`, 2026-07-06

## Why this matters

`AuthManager` deriva a chave Fernet que criptografa TODOS os tokens OAuth
armazenados (OpenAI, Anthropic, GitHub, etc.) a partir de um segredo gravado
em `~/.bauer/.auth_key`. Esse arquivo é criado com `write_text()`, que usa a
umask padrão do processo — em Linux/macOS com umask 022 o arquivo fica
**world-readable** (`-rw-r--r--`). Em qualquer máquina multi-usuário ou
container compartilhado, outro usuário local lê `.auth_key`, deriva a chave
Fernet e decripta todos os tokens. Além disso, o docstring afirma que a chave
é "baseada no machine ID", mas a implementação usa `secrets.token_hex(16)`
(um valor aleatório) — o comentário engana quem for auditar. O fix é pequeno
e de baixo risco: gravar o arquivo com permissão `0o600` (dono apenas) e
corrigir o docstring.

## Current state

- `bauer/auth.py` — camada de autenticação/persistência de tokens do
  `bauer serve` (1308 linhas). O método relevante está nas linhas 307–314:

```python
# bauer/auth.py:307-314
    def _get_or_create_key(self) -> str:
        """Chave de ofuscação baseada no machine ID."""
        key_file = self.base_dir / ".auth_key"
        if key_file.exists():
            return key_file.read_text().strip()
        key = secrets.token_hex(16)
        key_file.write_text(key)
        return key
```

- `self.base_dir` é `~/.bauer` (criado em `__init__`, linha 303:
  `self.base_dir.mkdir(parents=True, exist_ok=True)`).
- `import os` **pode não estar** no topo de `auth.py` — verifique antes de usar
  `os.chmod`. Se não estiver, adicione-o junto aos outros imports stdlib no
  topo do arquivo (não dentro da função).

### Convenções do repo a seguir
- Comentários e docstrings em português (o restante de `auth.py` é assim).
- Imports stdlib no topo do módulo, agrupados; evite import dentro de função
  a menos que seja para quebrar ciclo (não é o caso de `os`).

## Commands you will need

| Purpose   | Command                                              | Expected on success |
|-----------|------------------------------------------------------|---------------------|
| Testes    | `.venv/Scripts/python.exe -m pytest tests/test_auth*.py -q` | all pass       |
| Import OK | `.venv/Scripts/python.exe -c "import bauer.auth"`    | exit 0, sem saída   |

(Se `tests/test_auth*.py` não existir, use `tests/` inteiro filtrando por
`-k auth`. No Linux/macOS o interpretador é `.venv/bin/python`.)

## Scope

**In scope** (os únicos arquivos que você deve modificar):
- `bauer/auth.py`
- `tests/test_auth_permissions.py` (criar — ver Test plan)

**Out of scope** (NÃO tocar):
- A lógica de criptografia Fernet em si (`save`/`load` de tokens) — só a
  criação do arquivo de chave muda.
- Qualquer integração com `keyring` — está fora deste plano (é um follow-up
  maior; ver Maintenance notes). Este plano é só o hardening mínimo de
  permissão de arquivo.
- Migração de chaves `.auth_key` já existentes — o fix aplica `chmod` também
  no caminho de leitura (ver Step 1) para cobrir arquivos legados, mas não
  regenera a chave.

## Git workflow

- Branch: `advisor/007-auth-key-file-permissions`
- Estilo de commit: conventional commits (o repo usa `fix(scope): ...`).
  Exemplo do histórico: `fix(security): regex da xAI API key ...`.
  Sugestão: `fix(security): grava .auth_key com permissao 0o600 (nao world-readable)`
- NÃO faça push nem abra PR a menos que o operador peça.

## Steps

### Step 1: Gravar `.auth_key` com permissão 0o600 e garantir chmod no path legado

Em `bauer/auth.py`, altere `_get_or_create_key` (linhas 307–314) para:
1. corrigir o docstring (não é "machine ID", é um segredo aleatório local);
2. no ramo de criação, aplicar `os.chmod(key_file, 0o600)` logo após gravar;
3. no ramo de leitura (arquivo já existe), aplicar `os.chmod` best-effort para
   corrigir arquivos legados criados world-readable (dentro de try/except para
   não falhar em Windows/filesystems sem suporte a chmod POSIX).

Forma-alvo:

```python
    def _get_or_create_key(self) -> str:
        """Segredo local aleatório que deriva a chave de ofuscação dos tokens.

        Gravado em ~/.bauer/.auth_key com permissão 0o600 (só o dono lê) —
        senão outro usuário local poderia ler o segredo e decriptar os
        tokens OAuth armazenados. Não é derivado de machine ID; é um
        token_hex aleatório por instalação.
        """
        key_file = self.base_dir / ".auth_key"
        if key_file.exists():
            # Corrige permissão de arquivos legados criados com umask padrão.
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass  # filesystems sem chmod POSIX (ex.: alguns Windows)
            return key_file.read_text().strip()
        key = secrets.token_hex(16)
        key_file.write_text(key)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        return key
```

Se `import os` não existir no topo do arquivo, adicione-o.

**Verify**: `.venv/Scripts/python.exe -c "import bauer.auth"` → exit 0, sem erro.

### Step 2: Adicionar teste de permissão

Crie `tests/test_auth_permissions.py` conforme o Test plan abaixo.

**Verify**: `.venv/Scripts/python.exe -m pytest tests/test_auth_permissions.py -q` → all pass.

## Test plan

- Novo arquivo `tests/test_auth_permissions.py`. Modele a estrutura por
  qualquer teste existente que instancie `AuthManager` com `base_dir` em
  `tmp_path` (procure com `grep -rn "AuthManager(" tests/`).
- Casos a cobrir:
  1. **Criação**: instanciar `AuthManager(base_dir=tmp_path)`, verificar que
     `(tmp_path / ".auth_key").exists()` e que `oct(stat(...).st_mode & 0o777)
     == "0o600"`. **Pule a asserção de modo em Windows** — envolva em
     `if os.name != "nt":` porque `os.chmod` no Windows só honra o bit de
     leitura/escrita, não o modelo POSIX completo. Ainda assim, verifique que
     o arquivo foi criado e é legível pelo próprio processo.
  2. **Idempotência**: instanciar duas vezes com o mesmo `base_dir` retorna a
     mesma chave (o valor de `.auth_key` não muda).
- Verificação: `.venv/Scripts/python.exe -m pytest tests/test_auth_permissions.py -q`
  → all pass (2 testes).

## Done criteria

Machine-checkable. TODAS devem valer:

- [ ] `.venv/Scripts/python.exe -c "import bauer.auth"` sai 0
- [ ] `.venv/Scripts/python.exe -m pytest tests/test_auth_permissions.py -q` passa (2 novos testes)
- [ ] `.venv/Scripts/python.exe -m pytest tests/ -k auth -q` continua passando
- [ ] `grep -n "baseada no machine ID" bauer/auth.py` não retorna nada (docstring corrigido)
- [ ] `grep -n "os.chmod(key_file, 0o600)" bauer/auth.py` retorna 2 ocorrências
- [ ] Nenhum arquivo fora do in-scope modificado (`git status`)
- [ ] Linha de status deste plano atualizada em `plans/README.md`

## STOP conditions

Pare e reporte (não improvise) se:

- O código em `auth.py:307-314` não bater com o excerpt de "Current state"
  (o arquivo mudou desde que o plano foi escrito).
- `import os` já existir mas com alias incomum (ex.: `import os as _os`) — nesse
  caso use o alias existente em vez de adicionar um segundo import.
- A verificação de teste falhar duas vezes após um ajuste razoável.
- Você descobrir que `AuthManager` grava a chave em outro lugar além de
  `_get_or_create_key` (o segredo NÃO deve existir world-readable em nenhum
  caminho) — reporte para escopo maior.

## Maintenance notes

- **Follow-up deferido**: migração para `keyring` (armazenamento seguro do SO)
  foi deliberadamente deixada fora — é esforço M e muda o fluxo de storage.
  Este plano é o mitigador mínimo (permissão de arquivo). Se/quando o keyring
  entrar, `_get_or_create_key` some e a chave passa a viver no cofre do SO.
- O reviewer deve conferir que o `chmod` no ramo de leitura é best-effort
  (try/except) — em Windows/WSL/alguns filesystems `os.chmod` com modelo POSIX
  é no-op ou lança; não pode quebrar o boot do `bauer serve`.
- Tokens já vazados (se a chave foi lida por outro usuário antes deste fix)
  continuam comprometidos — recomende ao usuário **rotacionar** as API keys
  OAuth armazenadas após aplicar o fix, se a máquina for compartilhada.

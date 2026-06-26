"""Tools de sistema de arquivos: list/read/write/search/glob/diff e afins.

Mixin herdado por ToolRouter. Usa self._sandbox (core) e os limites de I/O
de .base. Os helpers _coerce_int/_require_prior_read/_mark_written moram aqui;
_syntax_check vem de .base (compartilhado com _patch_file no tool_router).
"""

from __future__ import annotations

import difflib
import re
import shutil
from pathlib import Path

from .base import (
    ToolError,
    _DEFAULT_READ_LINES,
    _MAX_FILE_BYTES,
    _MAX_READ_BYTES,
    _MAX_SEARCH_RESULTS,
    _syntax_check,
)


class FsToolsMixin:
    """Ferramentas de arquivo/diretorio dentro do workspace sandboxado."""

    def _list_dir(self, args: dict) -> str:
        path = args.get("path", ".")
        p = self._sandbox(str(path))

        if not p.exists():
            raise ToolError(f"Nao encontrado: '{path}'")
        if not p.is_dir():
            raise ToolError(f"'{path}' nao e um diretorio — use read_file para arquivos.")

        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        if not entries:
            return f"{path}/ (vazio)"

        lines = [f"Conteudo de {path}/"]
        for e in entries:
            suffix = "/" if e.is_dir() else ""
            size = f"  ({e.stat().st_size} bytes)" if e.is_file() else ""
            lines.append(f"  {e.name}{suffix}{size}")
        return "\n".join(lines)

    @staticmethod
    def _coerce_int(value, default: int, minimum: int) -> int:
        """Coage value para int >= minimum; default em falha. (G17.1)"""
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return n if n >= minimum else default

    def _read_file(self, args: dict) -> str:
        """Le arquivo com paginacao (offset/limit) + numeracao de linha + dedup. (G17.1)

        Espelha o read_file do Hermes/Claude Code:
          - offset (1-indexed) e limit selecionam uma janela de linhas
          - cada linha sai prefixada com seu numero (facilita patch/edit)
          - ceiling de tamanho de arquivo + cap de chars no output
          - dedup anti-loop: re-leitura identica de arquivo inalterado e bloqueada
        """
        path = args.get("path")
        if not path:
            raise ToolError("read_file requer 'path'.")
        offset = self._coerce_int(args.get("offset", 1), default=1, minimum=1)
        limit = self._coerce_int(args.get("limit", _DEFAULT_READ_LINES),
                                 default=_DEFAULT_READ_LINES, minimum=1)

        p = self._sandbox(str(path))
        if not p.exists():
            raise ToolError(f"Arquivo nao encontrado: '{path}'")
        if p.is_dir():
            raise ToolError(f"'{path}' e um diretorio — use list_dir.")

        size = p.stat().st_size
        if size > _MAX_FILE_BYTES:
            raise ToolError(
                f"Arquivo muito grande: {size} bytes (limite: {_MAX_FILE_BYTES}).\n"
                f"Use search_text/regex_search para localizar trechos, "
                f"ou read_file com offset+limit menores."
            )

        # ── Dedup anti-loop (G17.1) ───────────────────────────────────────
        # Se o modelo re-le a MESMA janela de um arquivo inalterado, devolve
        # stub; apos 2 hits, bloqueia para nao queimar o budget de iteracoes.
        resolved = str(p)
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        key = (offset, limit)
        tracked = self._read_tracker.get(resolved)
        if tracked and tracked["key"] == key and tracked["mtime"] == mtime:
            hits = tracked["hits"] + 1
            tracked["hits"] = hits
            if hits >= 2:
                raise ToolError(
                    f"BLOQUEADO: read_file('{path}', offset={offset}, limit={limit}) "
                    f"foi chamado {hits + 1}x e o arquivo NAO mudou. "
                    "Use o conteudo que voce ja leu — pare de reler o mesmo trecho."
                )
            return (
                f"[read_file] '{path}' inalterado desde a ultima leitura "
                f"(offset={offset}, limit={limit}). Reaproveite o resultado anterior."
            )

        raw = p.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"'{path}' parece ser binario — read_file so aceita texto UTF-8.")

        lines = text.splitlines()
        total = len(lines)
        start = offset - 1
        if total and start >= total:
            raise ToolError(
                f"offset {offset} esta alem do fim do arquivo "
                f"('{path}' tem {total} linha(s))."
            )
        window = lines[start:start + limit]
        end = start + len(window)  # exclusivo, 0-indexed

        width = max(len(str(end)), 1)
        body = "\n".join(f"{start + i + 1:>{width}}\t{ln}" for i, ln in enumerate(window))

        if len(body) > _MAX_READ_BYTES:
            raise ToolError(
                f"Leitura produziu {len(body)} chars (limite: {_MAX_READ_BYTES}).\n"
                f"Reduza 'limit' (atual: {limit}) ou avance 'offset' para ler menos linhas."
            )

        # Registra dedup + read-before-write (G17.2)
        self._read_tracker[resolved] = {"key": key, "mtime": mtime, "hits": 0}
        self._read_paths.add(resolved)

        header = f"# {path} — linhas {start + 1}-{end} de {total}"
        footer = ""
        if end < total:
            footer = (
                f"\n[... +{total - end} linha(s). Continue com "
                f"read_file('{path}', offset={end + 1}).]"
            )
        if not window:
            return f"{header}\n(arquivo vazio)"
        return f"{header}\n{body}{footer}"

    def _require_prior_read(self, p: Path, path: str, op: str) -> None:
        """Exige que um arquivo existente tenha sido lido antes de ser editado.

        Evita edicao/sobrescrita cega — o modelo precisa ter visto o conteudo
        atual (via read_file) nesta sessao. Arquivos novos sao isentos.
        """
        if str(p) not in self._read_paths:
            raise ToolError(
                f"{op}: '{path}' existe mas nao foi lido nesta sessao.\n"
                f"Leia com read_file('{path}') antes de edita-lo — "
                "editar as cegas corrompe arquivos."
            )

    def _mark_written(self, p: Path) -> None:
        """Apos escrever, o arquivo conta como 'lido' para o gate de
        read-before-write (o modelo conhece o conteudo que acabou de gravar).

        Limpa qualquer estado de dedup do arquivo: o conteudo mudou, entao a
        proxima read_file deve retornar conteudo real (nao um stub 'inalterado').
        """
        resolved = str(p)
        self._read_paths.add(resolved)
        self._read_tracker.pop(resolved, None)

    def _write_file(self, args: dict) -> str:
        path = args.get("path")
        content = args.get("content")
        overwrite = args.get("overwrite", False)

        if not path:
            raise ToolError("write_file requer 'path'.")
        if content is None:
            raise ToolError("write_file requer 'content'.")
        if not isinstance(overwrite, bool):
            raise ToolError("write_file: 'overwrite' deve ser true ou false.")

        p = self._sandbox(str(path))

        if p.exists() and not overwrite:
            raise ToolError(
                f"'{path}' ja existe e overwrite=false.\n"
                f"Leia o arquivo com read_file antes de sobrescrever.\n"
                f"Para sobrescrever: adicione \"overwrite\": true nos args."
            )
        # G17.2: sobrescrever arquivo existente exige leitura previa.
        if p.exists() and overwrite:
            self._require_prior_read(p, str(path), "write_file (overwrite)")

        p.parent.mkdir(parents=True, exist_ok=True)
        text = str(content)
        p.write_text(text, encoding="utf-8")
        self._mark_written(p)
        result = f"Gravado: '{path}' ({len(text)} chars)"
        # Verificação pós-write: o modelo recebe o erro de sintaxe IMEDIATAMENTE
        # em vez de descobrir 3 tool calls depois ao tentar executar o arquivo.
        syntax_err = _syntax_check(p, text)
        if syntax_err:
            result += (
                f"\n[ATENÇÃO — erro de sintaxe detectado] {syntax_err}\n"
                "Corrija com a tool patch antes de usar o arquivo."
            )
        return result

    def _search_text(self, args: dict) -> str:
        path = args.get("path", ".")
        pattern = args.get("pattern")

        if not pattern:
            raise ToolError("search_text requer 'pattern'.")

        p = self._sandbox(str(path))
        if not p.exists():
            raise ToolError(f"Nao encontrado: '{path}'")

        files = [p] if p.is_file() else sorted(p.rglob("*"))
        results: list[str] = []

        for f in files:
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.lower() in line.lower():
                    try:
                        rel = f.relative_to(self.workspace)
                    except ValueError:
                        rel = f
                    results.append(f"{rel}:{i}: {line.strip()}")
                    if len(results) >= _MAX_SEARCH_RESULTS:
                        results.append(f"... (limite de {_MAX_SEARCH_RESULTS} resultados atingido)")
                        return "\n".join(results)

        if not results:
            return f"Nenhum resultado para '{pattern}' em '{path}'"
        return "\n".join(results)

    def _create_dir(self, args: dict) -> str:
        path = args.get("path")
        if not path:
            raise ToolError("create_dir requer 'path'.")
        p = self._sandbox(str(path))
        p.mkdir(parents=True, exist_ok=True)
        return f"Diretorio criado: '{path}'"

    def _delete_file(self, args: dict) -> str:
        path = args.get("path")
        if not path:
            raise ToolError("delete_file requer 'path'.")
        confirm = args.get("confirm", False)
        if not isinstance(confirm, bool):
            raise ToolError("delete_file: 'confirm' deve ser true ou false.")
        if not confirm:
            raise ToolError(
                f"delete_file: operacao destrutiva — adicione \"confirm\": true para confirmar exclusao de '{path}'."
            )
        p = self._sandbox(str(path))
        if not p.exists():
            raise ToolError(f"Arquivo nao encontrado: '{path}'")
        if p.is_dir():
            raise ToolError(f"'{path}' e um diretorio. Use run_command com 'rm -rf' para remover diretorios.")
        p.unlink()
        return f"Arquivo removido: '{path}'"

    def _append_file(self, args: dict) -> str:
        path = args.get("path")
        content = args.get("content")
        if not path:
            raise ToolError("append_file requer 'path'.")
        if content is None:
            raise ToolError("append_file requer 'content'.")
        p = self._sandbox(str(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        text = str(content)
        with p.open("a", encoding="utf-8") as f:
            f.write(text)
        return f"Acrescentado em '{path}': {len(text)} chars"

    def _move_file(self, args: dict) -> str:
        src = args.get("src")
        dst = args.get("dst")
        overwrite = args.get("overwrite", False)
        if not src:
            raise ToolError("move_file requer 'src'.")
        if not dst:
            raise ToolError("move_file requer 'dst'.")
        if not isinstance(overwrite, bool):
            raise ToolError("move_file: 'overwrite' deve ser true ou false.")
        p_src = self._sandbox(str(src))
        p_dst = self._sandbox(str(dst))
        if not p_src.exists():
            raise ToolError(f"Origem nao encontrada: '{src}'")
        if p_dst.exists() and not overwrite:
            raise ToolError(
                f"'{dst}' ja existe e overwrite=false. Adicione \"overwrite\": true para sobrescrever."
            )
        p_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p_src), str(p_dst))
        return f"Movido: '{src}' → '{dst}'"

    def _diff_files(self, args: dict) -> str:
        path_a = args.get("path_a")
        path_b = args.get("path_b")
        context_lines = int(args.get("context_lines", 3))
        if not path_a:
            raise ToolError("diff_files requer 'path_a'.")
        if not path_b:
            raise ToolError("diff_files requer 'path_b'.")
        pa = self._sandbox(str(path_a))
        pb = self._sandbox(str(path_b))
        if not pa.exists():
            raise ToolError(f"Arquivo nao encontrado: '{path_a}'")
        if not pb.exists():
            raise ToolError(f"Arquivo nao encontrado: '{path_b}'")
        lines_a = pa.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        lines_b = pb.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        diff = list(difflib.unified_diff(
            lines_a, lines_b,
            fromfile=str(path_a),
            tofile=str(path_b),
            n=context_lines,
        ))
        if not diff:
            return f"Arquivos identicos: '{path_a}' e '{path_b}'"
        result = "".join(diff)
        if len(result) > _MAX_READ_BYTES:
            result = result[:_MAX_READ_BYTES] + "\n[... diff truncado]"
        return result

    def _glob_files(self, args: dict) -> str:
        pattern = args.get("pattern")
        base = args.get("path", ".")
        if not pattern:
            raise ToolError("glob_files requer 'pattern'.")
        p = self._sandbox(str(base))
        if not p.exists():
            raise ToolError(f"Diretorio nao encontrado: '{base}'")
        matches = sorted(p.glob(pattern))
        if not matches:
            return f"Nenhum arquivo encontrado com o padrao '{pattern}' em '{base}'"
        lines = []
        for m in matches[:200]:
            try:
                rel = m.relative_to(self.workspace)
            except ValueError:
                rel = m
            suffix = "/" if m.is_dir() else f"  ({m.stat().st_size} bytes)"
            lines.append(f"  {rel}{suffix}")
        result = f"Encontrados {len(matches)} arquivo(s) — padrao '{pattern}':\n" + "\n".join(lines)
        if len(matches) > 200:
            result += f"\n... (mostrando 200 de {len(matches)})"
        return result

    def _regex_search(self, args: dict) -> str:
        pattern = args.get("pattern")
        base = args.get("path", ".")
        flags_str = str(args.get("flags", "")).lower()
        if not pattern:
            raise ToolError("regex_search requer 'pattern'.")
        re_flags = 0
        if "i" in flags_str:
            re_flags |= re.IGNORECASE
        if "m" in flags_str:
            re_flags |= re.MULTILINE
        if "s" in flags_str:
            re_flags |= re.DOTALL
        try:
            compiled = re.compile(pattern, re_flags)
        except re.error as exc:
            raise ToolError(f"Regex inválida: {exc}") from exc

        p = self._sandbox(str(base))
        if not p.exists():
            raise ToolError(f"Nao encontrado: '{base}'")
        files = [p] if p.is_file() else sorted(p.rglob("*"))
        results: list[str] = []

        for f in files:
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if compiled.search(line):
                    try:
                        rel = f.relative_to(self.workspace)
                    except ValueError:
                        rel = f
                    results.append(f"{rel}:{i}: {line.strip()}")
                    if len(results) >= _MAX_SEARCH_RESULTS:
                        results.append(f"... (limite de {_MAX_SEARCH_RESULTS} resultados atingido)")
                        return "\n".join(results)

        if not results:
            return f"Nenhum resultado para regex '{pattern}' em '{base}'"
        return "\n".join(results)

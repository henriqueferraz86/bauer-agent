"""Known LSP server configurations."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LspServerConfig:
    """Configuration for a single LSP server."""
    cmd: list[str]
    lang: str
    install_hint: str = ""
    extra_env: dict[str, str] = field(default_factory=dict)


KNOWN_SERVERS: dict[str, LspServerConfig] = {
    "pyright": LspServerConfig(
        cmd=["pyright-langserver", "--stdio"],
        lang="python",
        install_hint="pip install pyright",
    ),
    "jedi": LspServerConfig(
        cmd=["jedi-language-server"],
        lang="python",
        install_hint="pip install jedi-language-server",
    ),
    "pylsp": LspServerConfig(
        cmd=["pylsp"],
        lang="python",
        install_hint="pip install python-lsp-server",
    ),
    "typescript": LspServerConfig(
        cmd=["typescript-language-server", "--stdio"],
        lang="typescript",
        install_hint="npm install -g typescript-language-server typescript",
    ),
    "rust-analyzer": LspServerConfig(
        cmd=["rust-analyzer"],
        lang="rust",
        install_hint="rustup component add rust-analyzer",
    ),
    "gopls": LspServerConfig(
        cmd=["gopls"],
        lang="go",
        install_hint="go install golang.org/x/tools/gopls@latest",
    ),
    "clangd": LspServerConfig(
        cmd=["clangd"],
        lang="c",
        install_hint="apt install clangd / brew install llvm",
    ),
}


def server_for_language(lang: str) -> LspServerConfig | None:
    """Return first server config that matches the given language."""
    for cfg in KNOWN_SERVERS.values():
        if cfg.lang == lang:
            return cfg
    return None


def server_for_file(file_path: str) -> LspServerConfig | None:
    """Return best LSP server for the given file by extension."""
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    lang_map = {
        "py": "python",
        "ts": "typescript",
        "tsx": "typescript",
        "js": "typescript",
        "jsx": "typescript",
        "rs": "rust",
        "go": "go",
        "c": "c",
        "cpp": "c",
        "h": "c",
    }
    lang = lang_map.get(ext)
    if lang:
        return server_for_language(lang)
    return None

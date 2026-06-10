"""Cliente MCP (Model Context Protocol) stdio puro — sem dependência do pacote 'mcp' (MCP-1).

Implementa JSON-RPC 2.0 sobre subprocess stdin/stdout conforme a spec MCP:
  https://spec.modelcontextprotocol.io/specification/

Suporta:
  - Handshake initialize / initialized
  - tools/list    — descobre tools disponíveis no servidor
  - tools/call    — executa uma tool
  - Gerenciamento de ciclo de vida do processo servidor (start/stop)
  - Timeout configurável por chamada

Não requer o pacote 'mcp' instalado — usa apenas stdlib (subprocess, json, threading).

Configuração em config.yaml:
    mcp:
      servers:
        meu_servidor:
          command: ["python", "-m", "meu_mcp_server"]
          env: {}          # variáveis de ambiente extras (opcional)
          timeout: 30      # timeout por chamada em segundos (opcional, default 30)

Uso programático:
    from bauer.mcp_client import McpClient, McpServerConfig

    cfg = McpServerConfig(
        name="filesystem",
        command=["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    )
    client = McpClient(cfg)
    client.start()
    tools = client.list_tools()
    result = client.call_tool("read_file", {"path": "/tmp/hello.txt"})
    client.stop()
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Exceções
# ---------------------------------------------------------------------------

class McpError(Exception):
    """Erro genérico do cliente MCP."""


class McpConnectionError(McpError):
    """Falha ao conectar / inicializar com o servidor MCP."""


class McpToolError(McpError):
    """Servidor retornou erro ao chamar uma tool."""


class McpTimeoutError(McpError):
    """Timeout ao aguardar resposta do servidor MCP."""


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

@dataclass
class McpServerConfig:
    """Configuração de um servidor MCP.

    Attributes:
        name: Identificador único do servidor (ex: 'filesystem').
        command: Comando + argumentos para iniciar o processo (ex: ['npx', '-y', '@mcp/server-fs']).
        env: Variáveis de ambiente extras para o processo.
        timeout: Timeout por chamada JSON-RPC em segundos.
        cwd: Diretório de trabalho do processo (None = herda do pai).
    """
    name: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    cwd: str | None = None


# ---------------------------------------------------------------------------
# McpClient — um processo servidor por instância
# ---------------------------------------------------------------------------

class McpClient:
    """Cliente MCP para um único servidor stdio.

    Thread-safe: usa lock interno para serializar chamadas JSON-RPC.
    Cada chamada espera a resposta correspondente pelo id.
    """

    _PROTOCOL_VERSION = "2024-11-05"
    _CLIENT_INFO = {"name": "bauer-agent", "version": "0.1"}

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._seq = 0
        self._initialized = False
        self._server_capabilities: dict = {}
        self._available_tools: list[dict] = []

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Inicia o processo servidor e realiza o handshake initialize."""
        if self._proc is not None:
            return  # já iniciado

        merged_env = {**os.environ, **self.config.env}
        try:
            self._proc = subprocess.Popen(
                self.config.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged_env,
                cwd=self.config.cwd,
                bufsize=0,
            )
        except FileNotFoundError as exc:
            raise McpConnectionError(
                f"Servidor MCP '{self.config.name}': comando nao encontrado: "
                f"{self.config.command[0]!r}. "
                f"Verifique se o executavel esta no PATH.\nDetalhe: {exc}"
            ) from exc
        except OSError as exc:
            raise McpConnectionError(
                f"Servidor MCP '{self.config.name}': falha ao iniciar processo: {exc}"
            ) from exc

        try:
            self._do_initialize()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        """Encerra o processo servidor graciosamente."""
        if self._proc is None:
            return
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        finally:
            self._proc = None
            self._initialized = False

    def __enter__(self) -> "McpClient":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def list_tools(self, force_refresh: bool = False) -> list[dict]:
        """Retorna lista de tools disponíveis no servidor.

        Args:
            force_refresh: Se True, consulta o servidor mesmo que já tenha cache.

        Returns:
            Lista de dicts: {name, description, inputSchema}.
        """
        self._ensure_started()
        if self._available_tools and not force_refresh:
            return self._available_tools

        resp = self._call("tools/list", {})
        raw = resp.get("result", {})
        self._available_tools = raw.get("tools", [])
        return self._available_tools

    def call_tool(self, tool_name: str, arguments: dict | None = None) -> str:
        """Chama uma tool no servidor MCP.

        Args:
            tool_name: Nome da tool (ex: 'read_file').
            arguments: Argumentos da tool como dict.

        Returns:
            Resultado como string (concatenação de blocks de texto).

        Raises:
            McpToolError: Servidor retornou erro.
            McpTimeoutError: Timeout aguardando resposta.
        """
        self._ensure_started()
        params = {
            "name": tool_name,
            "arguments": arguments or {},
        }
        resp = self._call("tools/call", params)

        # Verifica erro no nível JSON-RPC
        if "error" in resp:
            err = resp["error"]
            raise McpToolError(
                f"Servidor '{self.config.name}', tool '{tool_name}': "
                f"[{err.get('code', '?')}] {err.get('message', str(err))}"
            )

        result = resp.get("result", {})

        # isError no nível da tool
        if result.get("isError"):
            content = result.get("content", [])
            msg = _blocks_to_text(content) or str(result)
            raise McpToolError(
                f"Tool '{tool_name}' retornou erro: {msg}"
            )

        content = result.get("content", [])
        return _blocks_to_text(content)

    def server_info(self) -> dict:
        """Retorna capabilities do servidor (preenchido no handshake)."""
        return self._server_capabilities

    # ------------------------------------------------------------------
    # Handshake MCP
    # ------------------------------------------------------------------

    def _do_initialize(self) -> None:
        """Realiza o handshake initialize/initialized."""
        params = {
            "protocolVersion": self._PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "clientInfo": self._CLIENT_INFO,
        }
        resp = self._call("initialize", params)

        if "error" in resp:
            err = resp["error"]
            raise McpConnectionError(
                f"Servidor '{self.config.name}' rejeitou initialize: {err}"
            )

        self._server_capabilities = resp.get("result", {})
        # Notificação initialized (sem id — notificação unidirecional)
        self._notify("notifications/initialized", {})
        self._initialized = True

    # ------------------------------------------------------------------
    # JSON-RPC 2.0 transport
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    def _call(self, method: str, params: dict) -> dict:
        """Envia uma requisição JSON-RPC e aguarda a resposta correspondente."""
        with self._lock:
            req_id = self._next_id()
            message = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            }
            self._send(message)
            return self._recv(req_id)

    def _notify(self, method: str, params: dict) -> None:
        """Envia uma notificação JSON-RPC (sem id, sem resposta esperada)."""
        with self._lock:
            message = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
            self._send(message)

    def _send(self, message: dict) -> None:
        """Serializa e envia uma mensagem para o servidor via stdin."""
        if self._proc is None or self._proc.stdin is None:
            raise McpConnectionError(
                f"Servidor '{self.config.name}' nao iniciado."
            )
        data = json.dumps(message, ensure_ascii=False) + "\n"
        try:
            self._proc.stdin.write(data.encode("utf-8"))
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpConnectionError(
                f"Servidor '{self.config.name}' encerrou a conexao: {exc}"
            ) from exc

    def _recv(self, expected_id: int) -> dict:
        """Lê linhas do stdout do servidor até encontrar a resposta com o id esperado."""
        if self._proc is None or self._proc.stdout is None:
            raise McpConnectionError(
                f"Servidor '{self.config.name}' nao iniciado."
            )

        deadline = _monotonic() + self.config.timeout
        # Lê em thread separada para poder aplicar timeout sem bloquear o processo
        result_holder: list[dict | Exception] = []
        ev = threading.Event()

        def _reader() -> None:
            try:
                while True:
                    line = self._proc.stdout.readline()  # type: ignore[union-attr]
                    if not line:
                        result_holder.append(
                            McpConnectionError(
                                f"Servidor '{self.config.name}' fechou stdout "
                                f"aguardando resposta id={expected_id}."
                            )
                        )
                        ev.set()
                        return
                    line = line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        # Linha de log/debug — ignora
                        continue
                    # Ignora notificações (sem "id") e respostas para outros ids
                    if msg.get("id") == expected_id:
                        result_holder.append(msg)
                        ev.set()
                        return
                    # Notificação ou resposta fora de ordem — ignora
            except Exception as exc:
                result_holder.append(exc)
                ev.set()

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        remaining = deadline - _monotonic()
        signaled = ev.wait(timeout=max(remaining, 0.1))

        if not signaled:
            raise McpTimeoutError(
                f"Servidor '{self.config.name}': timeout de {self.config.timeout}s "
                f"aguardando resposta id={expected_id} (method pode ser lento ou servidor travou)."
            )

        response = result_holder[0]
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[return-value]

    def _ensure_started(self) -> None:
        if not self.is_running:
            self.start()


# ---------------------------------------------------------------------------
# McpManager — gerencia múltiplos servidores
# ---------------------------------------------------------------------------

class McpManager:
    """Gerencia um conjunto de servidores MCP configurados.

    Instancia McpClient sob demanda (lazy) e reutiliza conexões.

    Uso:
        manager = McpManager(configs=[
            McpServerConfig("fs", ["npx", "-y", "@mcp/server-filesystem", "/tmp"]),
        ])
        tools = manager.list_tools("fs")
        result = manager.call_tool("fs", "read_file", {"path": "/tmp/a.txt"})
        manager.stop_all()
    """

    def __init__(self, configs: list[McpServerConfig] | None = None) -> None:
        self._configs: dict[str, McpServerConfig] = {}
        self._http_configs: dict[str, dict] = {}  # {name: {url, headers, timeout}}
        self._clients: dict[str, Any] = {}        # McpClient | McpHttpClient
        for cfg in (configs or []):
            self.add_server(cfg)

    def add_server(self, cfg: McpServerConfig) -> None:
        """Adiciona configuração de servidor stdio (não inicia ainda)."""
        self._configs[cfg.name] = cfg

    def add_http_server(
        self,
        name: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Adiciona configuração de servidor MCP via HTTP/SSE (não inicia ainda)."""
        self._http_configs[name] = {
            "url": url,
            "headers": headers or {},
            "timeout": timeout,
        }

    def remove_server(self, name: str) -> None:
        """Remove e encerra servidor (stdio ou HTTP)."""
        self.stop(name)
        self._configs.pop(name, None)
        self._http_configs.pop(name, None)

    def server_names(self) -> list[str]:
        """Retorna nomes de todos os servidores configurados (stdio + HTTP)."""
        return sorted(set(self._configs) | set(self._http_configs))

    def get_client(self, name: str) -> "McpClient | Any":
        """Retorna cliente para o servidor, iniciando se necessário.

        Se o servidor tem ``url`` configurado em vez de ``command``, retorna
        um :class:`~bauer.mcp_http_client.McpHttpClient` (HTTP/SSE transport).
        """
        if name not in self._configs and name not in self._http_configs:
            raise McpError(
                f"Servidor MCP '{name}' nao configurado. "
                f"Servidores disponíveis: {self.server_names()}"
            )

        # HTTP client path
        if name in self._http_configs:
            if name not in self._clients:
                from .mcp_http_client import McpHttpClient
                cfg = self._http_configs[name]
                client = McpHttpClient(
                    cfg["url"],
                    headers=cfg.get("headers") or {},
                    timeout=float(cfg.get("timeout", 30)),
                )
                self._clients[name] = client
            return self._clients[name]

        # Stdio client path
        if name not in self._clients or not self._clients[name].is_running:
            cfg = self._configs[name]
            client = McpClient(cfg)
            client.start()
            self._clients[name] = client
        return self._clients[name]

    def list_tools(self, server_name: str, force_refresh: bool = False) -> list[dict]:
        """Lista tools do servidor."""
        return self.get_client(server_name).list_tools(force_refresh=force_refresh)

    def list_all_tools(self) -> dict[str, list[dict]]:
        """Lista tools de todos os servidores configurados.

        Retorna dict {server_name: [tool_dicts]}.
        Servidores que falharem são omitidos (não levantam exceção).
        """
        result: dict[str, list[dict]] = {}
        for name in self._configs:
            try:
                result[name] = self.list_tools(name)
            except Exception:
                result[name] = []
        return result

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict | None = None,
    ) -> str:
        """Chama tool em servidor específico."""
        return self.get_client(server_name).call_tool(tool_name, arguments)

    def stop(self, name: str) -> None:
        """Encerra cliente de um servidor."""
        client = self._clients.pop(name, None)
        if client:
            client.stop()

    def stop_all(self) -> None:
        """Encerra todos os clientes."""
        for name in list(self._clients):
            self.stop(name)

    def __enter__(self) -> "McpManager":
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop_all()

    @classmethod
    def from_config(cls, mcp_config: Any) -> "McpManager":
        """Constrói McpManager a partir de um objeto de config (McpSection ou dict).

        Args:
            mcp_config: McpSection do config_loader ou dict {name: {command, env, timeout}}.
        """
        manager = cls()
        if mcp_config is None:
            return manager

        # Suporta tanto McpSection (Pydantic) quanto dict puro
        if hasattr(mcp_config, "servers"):
            servers = mcp_config.servers or {}
        elif isinstance(mcp_config, dict):
            servers = mcp_config.get("servers", {})
        else:
            return manager

        for name, srv in servers.items():
            if isinstance(srv, dict):
                url = srv.get("url") or None
                command = srv.get("command", [])
                if isinstance(command, str):
                    command = command.split()
                env = srv.get("env", {}) or {}
                timeout = float(srv.get("timeout", 30))
                cwd = srv.get("cwd") or None
                headers = srv.get("headers", {}) or {}
            elif hasattr(srv, "url") and getattr(srv, "url", None):
                url = srv.url
                command = []
                env = {}
                timeout = float(getattr(srv, "timeout", 30))
                cwd = None
                headers = dict(getattr(srv, "headers", {}) or {})
            elif hasattr(srv, "command"):
                url = None
                command = srv.command
                if isinstance(command, str):
                    command = command.split()
                env = dict(getattr(srv, "env", {}) or {})
                timeout = float(getattr(srv, "timeout", 30))
                cwd = getattr(srv, "cwd", None)
                headers = {}
            else:
                continue

            # Route: HTTP transport if url is set
            if url:
                manager.add_http_server(name, url, headers=headers, timeout=timeout)
                continue

            if not command:
                continue

            manager.add_server(McpServerConfig(
                name=name,
                command=command,
                env=env,
                timeout=timeout,
                cwd=cwd,
            ))

        return manager


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _blocks_to_text(content: list | str | None) -> str:
    """Converte content MCP para string.

    MCP retorna content como lista de blocks:
    [{type: 'text', text: '...'}, {type: 'image', ...}]
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "image":
                    parts.append(f"[imagem: {block.get('mimeType', 'image')}]")
                elif "text" in block:
                    parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def _monotonic() -> float:
    import time
    return time.monotonic()

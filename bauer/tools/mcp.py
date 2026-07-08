"""MCP tools: mcp_call (stdio JSON-RPC) via ..mcp_client + resolucao de server."""

from __future__ import annotations

import json

from .base import ToolError


class McpToolsMixin:

    def _mcp_call(self, args: dict) -> str:
        """Chama tool em servidor MCP via stdio (JSON-RPC 2.0 puro — sem pacote 'mcp').

        Usa McpClient nativo do Bauer. Não requer pip install mcp.

        Configuração em config.yaml:
            mcp:
              servers:
                meu_servidor:
                  command: ["python", "-m", "meu_mcp_server"]
                  timeout: 30

        Ou via variável de ambiente:
            MCP_SERVER_MEU_SERVIDOR="python -m meu_mcp_server"
        """
        server_name = args.get("server", "").strip()
        tool_name = args.get("tool", "").strip()
        arguments = args.get("arguments", {})

        if not server_name:
            raise ToolError("mcp_call requer 'server'.")
        if not tool_name:
            raise ToolError("mcp_call requer 'tool'.")
        if not isinstance(arguments, dict):
            try:
                arguments = json.loads(str(arguments))
            except Exception:
                raise ToolError("mcp_call: 'arguments' deve ser um objeto JSON.")

        # Resolve configuração do servidor
        if "_get_mcp_server_cmd" in self.__dict__:
            import asyncio
            server_cmd = self._get_mcp_server_cmd(server_name)
            legacy_call = self._mcp_call_legacy_async(server_cmd, tool_name, arguments)
            try:
                return asyncio.run(legacy_call)
            finally:
                legacy_call.close()

        server_cmd, server_env, server_timeout = self._resolve_mcp_server(server_name)

        from ..mcp_client import McpClient, McpServerConfig, McpError, McpToolError, McpTimeoutError
        cfg = McpServerConfig(
            name=server_name,
            command=server_cmd,
            env=server_env,
            timeout=server_timeout,
        )
        try:
            with McpClient(cfg) as client:
                return client.call_tool(tool_name, arguments)
        except McpToolError as exc:
            raise ToolError(str(exc)) from exc
        except McpTimeoutError as exc:
            raise ToolError(str(exc)) from exc
        except McpError as exc:
            raise ToolError(
                f"mcp_call: erro de conexao com '{server_name}': {exc}"
            ) from exc
        except Exception as exc:
            raise ToolError(
                f"mcp_call: erro inesperado chamando '{tool_name}' em '{server_name}': {exc}"
            ) from exc

    def _get_mcp_server_cmd(self, server_name: str) -> list[str]:
        """Compatibilidade com a API MCP anterior que retornava apenas o comando."""
        server_cmd, _, _ = self._resolve_mcp_server(server_name)
        return server_cmd

    async def _mcp_call_legacy_async(
        self,
        server_cmd: list[str],
        tool_name: str,
        arguments: dict,
    ) -> str:
        """Ponte para testes/extensoes que ainda sobrescrevem o cliente MCP legado."""
        raise ToolError(
            "mcp_call legado nao esta disponivel; use a configuracao MCP nativa do Bauer."
        )

    def _resolve_mcp_server(
        self, server_name: str
    ) -> tuple[list[str], dict[str, str], float]:
        """Resolve comando, env e timeout de um servidor MCP.

        Ordem de busca:
        1. Variável de ambiente: MCP_SERVER_<NAME>="python -m meu_servidor"
        2. config.yaml → mcp.servers.<name>
        3. Atributo legado self._mcp_config (compat)

        Returns:
            (command, env, timeout)
        """
        import os

        env_key = f"MCP_SERVER_{server_name.upper().replace('-', '_')}"
        env_val = os.environ.get(env_key, "")
        if env_val:
            return env_val.split(), {}, 30.0

        # Tenta McpSection do config_loader (injetado via self._mcp_config)
        mcp_config = getattr(self, "_mcp_config", None)
        if mcp_config is not None:
            servers = getattr(mcp_config, "servers", None) or {}
            if server_name in servers:
                srv = servers[server_name]
                if hasattr(srv, "command"):
                    # McpServerEntry (Pydantic)
                    cmd = srv.command if isinstance(srv.command, list) else srv.command.split()
                    env = dict(getattr(srv, "env", {}) or {})
                    timeout = float(getattr(srv, "timeout", 30))
                    return cmd, env, timeout
                elif isinstance(srv, dict) and "command" in srv:
                    cmd = srv["command"]
                    if isinstance(cmd, str):
                        cmd = cmd.split()
                    env = dict(srv.get("env", {}) or {})
                    timeout = float(srv.get("timeout", 30))
                    return cmd, env, timeout

        raise ToolError(
            f"Servidor MCP '{server_name}' nao configurado.\n"
            "Configure via:\n"
            f"  1. Variavel de ambiente: {env_key}=python -m meu_servidor\n"
            "  2. config.yaml:\n"
            "       mcp:\n"
            "         servers:\n"
            f"           {server_name}:\n"
            "             command: [\"python\", \"-m\", \"meu_servidor\"]\n"
            "             timeout: 30"
        )

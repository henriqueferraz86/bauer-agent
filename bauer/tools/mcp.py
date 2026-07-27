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

    def _mcp_list_servers(self, args: dict) -> str:
        """Lista os servidores MCP configurados, SEM iniciar nenhum deles.

        Existe porque `mcp_call` era uma porta opaca: o modelo via uma tool que
        pede `server` e `tool` e nada dizia quais valores existem. Sem isto, um
        servidor configurado só era usado se o usuário digitasse o nome na
        mensagem.

        Descoberta é PULL, sob demanda — não um inventário sempre presente no
        system prompt. O custo de prompt não cresce com o número de servidores.
        """
        import os

        found: dict[str, dict] = {}

        mcp_config = getattr(self, "_mcp_config", None)
        servers = getattr(mcp_config, "servers", None) or {} if mcp_config is not None else {}
        for name, srv in servers.items():
            if hasattr(srv, "command"):
                cmd = srv.command if isinstance(srv.command, list) else str(srv.command).split()
                env = dict(getattr(srv, "env", {}) or {})
                timeout = float(getattr(srv, "timeout", 30))
            elif isinstance(srv, dict) and "command" in srv:
                cmd = srv["command"]
                cmd = cmd.split() if isinstance(cmd, str) else cmd
                env = dict(srv.get("env", {}) or {})
                timeout = float(srv.get("timeout", 30))
            else:
                continue
            found[name] = {"command": cmd, "env": env, "timeout": timeout, "origem": "config.yaml"}

        # Env vars vencem o config (mesma ordem de _resolve_mcp_server).
        for key, val in os.environ.items():
            if not key.startswith("MCP_SERVER_") or not val.strip():
                continue
            name = key[len("MCP_SERVER_"):].lower()
            found[name] = {"command": val.split(), "env": {}, "timeout": 30.0,
                           "origem": f"env {key}"}

        if not found:
            return (
                "[mcp_list_servers] Nenhum servidor MCP configurado.\n"
                "Configure em config.yaml (mcp.servers) ou via MCP_SERVER_<NOME>."
            )

        lines = [f"[mcp_list_servers] {len(found)} servidor(es) configurado(s):"]
        for name in sorted(found):
            info = found[name]
            lines.append(f"\n- {name}  ({info['origem']}, timeout {info['timeout']:.0f}s)")
            lines.append(f"  comando: {' '.join(info['command'])}")
            if info["env"]:
                # Só os NOMES das variáveis: env de servidor MCP é onde moram
                # api keys, e o retorno de tool vai inteiro para o provider.
                lines.append(f"  env: {', '.join(sorted(info['env']))} (valores omitidos)")
        lines.append("\nUse mcp_list_tools(server=...) para ver o que cada um oferece.")
        return "\n".join(lines)

    def _mcp_list_tools(self, args: dict) -> str:
        """Lista as tools que um servidor MCP oferece, com schema de argumentos.

        Inicia o servidor, faz o handshake e chama tools/list — mesmo caminho do
        `mcp_call`, então tem o mesmo custo e os mesmos modos de falha.
        """
        server_name = str(args.get("server", "") or "").strip()
        if not server_name:
            raise ToolError("mcp_list_tools requer 'server'. Use mcp_list_servers para ver os nomes.")

        server_cmd, server_env, server_timeout = self._resolve_mcp_server(server_name)

        from ..mcp_client import McpClient, McpServerConfig, McpError, McpTimeoutError
        cfg = McpServerConfig(
            name=server_name,
            command=server_cmd,
            env=server_env,
            timeout=server_timeout,
        )
        try:
            with McpClient(cfg) as client:
                tools = client.list_tools()
        except McpTimeoutError as exc:
            raise ToolError(str(exc)) from exc
        except McpError as exc:
            raise ToolError(
                f"mcp_list_tools: erro de conexao com '{server_name}': {exc}"
            ) from exc
        except Exception as exc:
            raise ToolError(
                f"mcp_list_tools: erro inesperado listando tools de '{server_name}': {exc}"
            ) from exc

        if not tools:
            return f"[mcp_list_tools] Servidor '{server_name}' nao expoe nenhuma tool."

        lines = [f"[mcp_list_tools] '{server_name}' expoe {len(tools)} tool(s):"]
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name", "?")
            desc = (tool.get("description") or "").strip().replace("\n", " ")
            if len(desc) > 200:
                desc = desc[:197] + "..."
            lines.append(f"\n- {name}: {desc}" if desc else f"\n- {name}")
            schema = tool.get("inputSchema") or {}
            props = schema.get("properties") or {} if isinstance(schema, dict) else {}
            if props:
                required = set(schema.get("required") or [])
                campos = [
                    f"{k}{'' if k in required else '?'}:{(v or {}).get('type', 'any')}"
                    for k, v in props.items()
                ]
                lines.append(f"  args: {', '.join(campos)}")
        lines.append(f"\nChame com mcp_call(server=\"{server_name}\", tool=..., arguments={{...}}).")
        return "\n".join(lines)

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

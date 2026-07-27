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

        from ..mcp_client import McpError, McpToolError, McpTimeoutError
        try:
            with self._mcp_client_for(server_name) as client:
                return client.call_tool(tool_name, arguments)
        except McpToolError as exc:
            raise ToolError(str(exc)) from exc
        except McpTimeoutError as exc:
            raise ToolError(str(exc)) from exc
        except McpError as exc:
            raise ToolError(
                f"mcp_call: erro de conexao com '{server_name}': {exc}"
                + self._sufixo_dica(exc)
            ) from exc
        except Exception as exc:
            raise ToolError(
                f"mcp_call: erro inesperado chamando '{tool_name}' em '{server_name}': {exc}"
                + self._sufixo_dica(exc)
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
        for name in servers:
            try:
                info = self._resolve_mcp_transport(name)
            except ToolError:
                continue
            info["origem"] = "config.yaml"
            found[name] = info

        # Env vars vencem o config (mesma ordem de _resolve_mcp_transport).
        for key, val in os.environ.items():
            if not key.startswith("MCP_SERVER_") or not val.strip():
                continue
            name = key[len("MCP_SERVER_"):].lower()
            try:
                info = self._resolve_mcp_transport(name)
            except ToolError:
                continue
            info["origem"] = f"env {key}"
            found[name] = info

        if not found:
            return (
                "[mcp_list_servers] Nenhum servidor MCP configurado.\n"
                "Configure em config.yaml (mcp.servers) ou via MCP_SERVER_<NOME>."
            )

        lines = [f"[mcp_list_servers] {len(found)} servidor(es) configurado(s):"]
        for name in sorted(found):
            info = found[name]
            lines.append(f"\n- {name}  ({info['origem']}, timeout {info['timeout']:.0f}s)")
            if info["kind"] == "http":
                lines.append(f"  url: {info['url']}  [remoto]")
                if info["headers"]:
                    # Só os NOMES: header de MCP remoto carrega Authorization.
                    lines.append(f"  headers: {', '.join(sorted(info['headers']))} (valores omitidos)")
                continue
            lines.append(f"  comando: {' '.join(info['command'])}")
            if info.get("cwd"):
                lines.append(f"  cwd: {info['cwd']}")
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

        from ..mcp_client import McpError, McpTimeoutError
        try:
            with self._mcp_client_for(server_name) as client:
                tools = client.list_tools()
        except McpTimeoutError as exc:
            raise ToolError(str(exc)) from exc
        except McpError as exc:
            raise ToolError(
                f"mcp_list_tools: erro de conexao com '{server_name}': {exc}"
                + self._sufixo_dica(exc)
            ) from exc
        except Exception as exc:
            raise ToolError(
                f"mcp_list_tools: erro inesperado listando tools de '{server_name}': {exc}"
                + self._sufixo_dica(exc)
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

    @classmethod
    def _sufixo_dica(cls, exc: Exception) -> str:
        """A dica formatada para colar no fim de uma mensagem de erro."""
        dica = cls._mcp_dica(exc)
        return f"\n  -> {dica}" if dica else ""

    @staticmethod
    def _mcp_dica(exc: Exception) -> str:
        """Traduz a falha de um servidor MCP no PROXIMO PASSO do usuario.

        A causa do SO ja vinha na mensagem ("politica de Controle de Aplicativo
        bloqueou este arquivo"), mas dizer o que aconteceu nao e dizer o que
        fazer — e quem esbarra nisso costuma estar no meio de outra tarefa.
        """
        texto = str(exc).lower()
        if "controle de aplicativo" in texto or "application control" in texto or "4551" in texto:
            return (
                "O binario nao e assinado e a politica de aplicativo do Windows "
                "(Smart App Control/WDAC) recusa executa-lo. Baixar de novo nao "
                "resolve — a politica olha a assinatura. Saidas: rodar via WSL "
                "(`wsl -e /caminho/servidor`), via Docker, ou trocar por um "
                "servidor MCP instalavel por pip/npx."
            )
        # As duas grafias do mesmo erro: o Windows localiza a mensagem, entao
        # "cannot find the file" e "nao pode encontrar o arquivo" sao o WinError 2.
        if any(t in texto for t in (
            "no such file", "cannot find", "not found", "nao encontrado",
            "nao pode encontrar", "não pode encontrar", "errno 2", "winerror 2",
        )):
            return (
                "O comando nao foi encontrado. Confira o primeiro item de "
                "`command` no config: use caminho absoluto, ou garanta que o "
                "executavel esta no PATH do processo que roda o Bauer."
            )
        if "permission denied" in texto or "acesso negado" in texto or "errno 13" in texto:
            return (
                "Sem permissao de execucao. Em Linux/macOS, `chmod +x` no "
                "binario; se persistir, e politica de execucao do sistema."
            )
        if "timeout" in texto:
            return (
                "O processo subiu mas nao respondeu ao handshake dentro do "
                "timeout. Rode o comando na mao para ver o que ele imprime, e "
                "confirme que ele fala MCP por stdio (e nao HTTP)."
            )
        if "invalid json" in texto or "jsonrpc" in texto or "sse" in texto:
            return (
                "O servidor respondeu algo que nao e JSON-RPC valido. Se for "
                "remoto, confirme que a `url` aponta para o endpoint MCP e nao "
                "para a pagina do projeto."
            )
        return ""

    def _resolve_mcp_transport(self, server_name: str) -> dict:
        """Resolve o transporte de um servidor: stdio (processo) ou http (remoto).

        `_resolve_mcp_server` continua existindo para quem só quer o comando, mas
        perde duas coisas que importam: o `cwd` (que o config aceita, o
        McpServerConfig suporta, e o exemplo do Graphify EXIGE para apontar a
        raiz do repo) e servidores `url`, que nem passavam pelo schema.

        Returns:
            {"kind": "stdio", "command", "env", "timeout", "cwd"} ou
            {"kind": "http", "url", "headers", "timeout"}
        """
        import os

        env_key = f"MCP_SERVER_{server_name.upper().replace('-', '_')}"
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            if env_val.startswith(("http://", "https://")):
                return {"kind": "http", "url": env_val, "headers": {}, "timeout": 30.0}
            return {"kind": "stdio", "command": env_val.split(), "env": {},
                    "timeout": 30.0, "cwd": None}

        mcp_config = getattr(self, "_mcp_config", None)
        servers = getattr(mcp_config, "servers", None) or {} if mcp_config is not None else {}
        srv = servers.get(server_name)
        if srv is not None:
            get = (lambda k, d=None: getattr(srv, k, d)) if hasattr(srv, "command") \
                else (lambda k, d=None: srv.get(k, d))
            url = get("url")
            timeout = float(get("timeout", 30) or 30)
            if url:
                return {"kind": "http", "url": str(url),
                        "headers": dict(get("headers", {}) or {}), "timeout": timeout}
            cmd = get("command")
            if cmd:
                cmd = cmd.split() if isinstance(cmd, str) else list(cmd)
                return {"kind": "stdio", "command": cmd, "env": dict(get("env", {}) or {}),
                        "timeout": timeout, "cwd": get("cwd")}

        raise ToolError(
            f"Servidor MCP '{server_name}' nao configurado.\n"
            "Configure via:\n"
            f"  1. Variavel de ambiente: {env_key}=python -m meu_servidor (ou uma URL http)\n"
            "  2. config.yaml:\n"
            "       mcp:\n"
            "         servers:\n"
            f"           {server_name}:\n"
            "             command: [\"python\", \"-m\", \"meu_servidor\"]   # local\n"
            "             # ou, para servidor remoto:\n"
            "             # url: https://exemplo/mcp"
        )

    def _mcp_client_for(self, server_name: str):
        """Cliente MCP pronto para uso em `with`, no transporte configurado."""
        t = self._resolve_mcp_transport(server_name)
        if t["kind"] == "http":
            from ..mcp_http_client import McpHttpClient
            return McpHttpClient(t["url"], headers=t["headers"], timeout=t["timeout"])
        from ..mcp_client import McpClient, McpServerConfig
        return McpClient(McpServerConfig(
            name=server_name,
            command=t["command"],
            env=t["env"],
            timeout=t["timeout"],
            cwd=t["cwd"],
        ))

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

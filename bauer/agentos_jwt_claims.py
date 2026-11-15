"""Strict JWT claim checks for the optional AgentOS isolation profile."""

from __future__ import annotations

from starlette.responses import JSONResponse


class AgentOSJWTClaimsMiddleware:
    def __init__(self, app, key: str, audience: str, issuer: str):
        self.app = app
        self.key = key
        self.audience = audience
        self.issuer = issuer

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await self.app(scope, receive, send)
            return
        if scope["method"] == "GET" and scope["path"] in {"/health", "/info"}:
            await self.app(scope, receive, send)
            return

        import jwt

        headers = [value for name, value in scope["headers"] if name.lower() == b"authorization"]
        try:
            if len(headers) != 1:
                raise ValueError("authorization")
            scheme, token = headers[0].decode("ascii").split(" ", 1)
            if scheme.lower() != "bearer" or not token or " " in token:
                raise ValueError("authorization")
            payload = jwt.decode(
                token,
                self.key,
                algorithms=["HS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["sub", "exp", "iss", "aud"]},
            )
            subject = payload.get("sub")
            scopes = payload.get("scopes")
            if not isinstance(subject, str) or not subject.strip() or subject != subject.strip() or len(subject) > 256:
                raise ValueError("subject")
            if not isinstance(scopes, list) or not scopes or any(not isinstance(s, str) for s in scopes):
                raise ValueError("scopes")
            if "agent_os:admin" in scopes:
                raise ValueError("admin scope")
        except (ValueError, UnicodeDecodeError, jwt.InvalidTokenError):
            await JSONResponse({"detail": "credencial inválida"}, status_code=401)(scope, receive, send)
            return
        scope.setdefault("state", {})["bauer_jwt_identity"] = {
            "subject": subject,
            "scopes": sorted(set(scopes)),
        }
        await self.app(scope, receive, send)

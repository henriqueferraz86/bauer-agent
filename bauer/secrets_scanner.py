"""Scanner de segredos — detecta e redige tokens/keys em output de tools e logs.

Detecta: API keys (OpenAI, Anthropic, Groq, AWS, GitHub), JWTs, Bearer tokens,
         PEM private keys, passwords em URLs, tokens genéricos de alta entropia.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ─── Padrões de detecção ──────────────────────────────────────────────────────

@dataclass
class SecretPattern:
    name: str
    pattern: re.Pattern
    severity: str = "high"   # high | medium | low


_PATTERNS: list[SecretPattern] = [
    # OpenAI / Anthropic / Groq / xAI
    SecretPattern("OpenAI API Key",    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    SecretPattern("OpenAI Project Key",re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{20,}\b")),
    SecretPattern("Anthropic API Key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    SecretPattern("xAI API Key",       re.compile(r"\bxai-[A-Za-z0-9_\-]{20,}\b")),

    # GitHub
    SecretPattern("GitHub Token (classic)", re.compile(r"\bghp_[A-Za-z0-9]{36,}\b")),
    SecretPattern("GitHub Token (fine-grained)", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b")),
    SecretPattern("GitHub OAuth",      re.compile(r"\bgho_[A-Za-z0-9]{36,}\b")),
    SecretPattern("GitHub Actions",    re.compile(r"\bghs_[A-Za-z0-9]{36,}\b")),
    SecretPattern("Copilot Session Token", re.compile(r"\btid=[A-Za-z0-9_\-]{20,}\b")),

    # AWS
    SecretPattern("AWS Access Key",    re.compile(r"\bAKIA[A-Z0-9]{16}\b")),
    SecretPattern("AWS Secret Key",    re.compile(r"(?i)aws[_\-]?secret[_\-]?(?:access[_\-]?)?key\s*[=:\"']\s*[A-Za-z0-9/+]{40}\b")),

    # Google / GCP
    SecretPattern("Google API Key",    re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b")),
    SecretPattern("GCP Service Account", re.compile(r"\"type\"\s*:\s*\"service_account\"")),

    # JWT
    SecretPattern("JWT Token",         re.compile(r"\bey[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\b")),

    # Tokens genéricos em variáveis
    SecretPattern("Generic API Key var", re.compile(
        r'(?i)(?:api[_\-]?key|apikey|secret[_\-]?key|access[_\-]?token|auth[_\-]?token|bearer[_\-]?token)'
        r'\s*[=:\"\']\s*["\']?([A-Za-z0-9_\-]{20,})["\']?'
    ), severity="medium"),

    # Passwords em URLs
    SecretPattern("Password in URL",   re.compile(
        r"(?i)(?:https?|ftp)://[^:@\s]+:([^@\s]{6,})@"
    ), severity="medium"),

    # PEM private keys
    SecretPattern("Private Key (PEM)", re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
    )),

    # Groq / Mistral / Together / DeepSeek / OpenRouter
    SecretPattern("Groq API Key",      re.compile(r"\bgsk_[A-Za-z0-9]{50,}\b")),
    SecretPattern("HuggingFace Token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),

    # Tokens de alta entropia (heurística) — apenas se precedidos de keyword suspeita
    SecretPattern("High-entropy token", re.compile(
        r'(?i)(?:token|password|passwd|pwd|secret|credential|key)\s*[=:\"\']\s*["\']?([A-Za-z0-9+/=_\-]{32,})["\']?'
    ), severity="medium"),
]


# ─── Scanner ──────────────────────────────────────────────────────────────────

@dataclass
class ScanResult:
    found: bool
    matches: list[dict] = field(default_factory=list)
    redacted_text: str = ""


def scan(text: str, redact: bool = True) -> ScanResult:
    """Escaneia `text` em busca de segredos.

    Args:
        text: Texto a escanear.
        redact: Se True, substitui ocorrências por [REDACTED:<nome>].

    Returns:
        ScanResult com found, matches e redacted_text.
    """
    matches: list[dict] = []
    result_text = text

    for pat in _PATTERNS:
        for m in pat.pattern.finditer(text):
            matched_value = m.group(0)
            # Para padrões de captura, usar o grupo 1 se disponível
            try:
                captured = m.group(1)
                if captured and len(captured) >= 16:
                    matched_value = captured
            except IndexError:
                pass

            matches.append({
                "name": pat.name,
                "severity": pat.severity,
                "start": m.start(),
                "end": m.end(),
                "preview": matched_value[:8] + "..." if len(matched_value) > 8 else matched_value,
            })

        if redact:
            result_text = pat.pattern.sub(f"[REDACTED:{pat.name.replace(' ', '_')}]", result_text)

    return ScanResult(
        found=bool(matches),
        matches=matches,
        redacted_text=result_text if redact else text,
    )


def redact(text: str) -> str:
    """Versão rápida — retorna texto com segredos substituídos por [REDACTED]."""
    return scan(text, redact=True).redacted_text


def has_secrets(text: str) -> bool:
    """Retorna True se houver segredos detectados (sem redação)."""
    for pat in _PATTERNS:
        if pat.pattern.search(text):
            return True
    return False

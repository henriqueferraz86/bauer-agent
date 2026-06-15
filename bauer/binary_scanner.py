"""Detecção de binários e conteúdo suspeito em outputs de tools.

Proteção adicional de segurança: identifica quando uma tool retorna dados
que parecem executáveis (ELF, PE, Mach-O), shellcode patterns ou scripts
ofuscados via base64, evitando que o agente processe ou retransmita conteúdo
potencialmente malicioso sem aviso explícito.

Integrado ao ToolRouter como pós-processamento opcional (risk_level="high").
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from typing import Sequence


# ---------------------------------------------------------------------------
# Magic bytes de formatos executáveis conhecidos
# ---------------------------------------------------------------------------

_MAGIC_SIGNATURES: list[tuple[bytes, str, str]] = [
    # (magic_bytes, format_name, description)
    (b"\x7fELF",           "ELF",    "Linux/Unix executable"),
    (b"MZ",                "PE",     "Windows PE executable"),
    (b"\xfe\xed\xfa\xce", "Mach-O", "macOS executable (32-bit)"),
    (b"\xfe\xed\xfa\xcf", "Mach-O", "macOS executable (64-bit)"),
    (b"\xce\xfa\xed\xfe", "Mach-O", "macOS executable (32-bit LE)"),
    (b"\xcf\xfa\xed\xfe", "Mach-O", "macOS executable (64-bit LE)"),
    (b"\xca\xfe\xba\xbe", "Mach-O", "macOS universal binary"),
    (b"#!",               "Script", "Unix shebang script"),
    (b"\x4d\x5a",         "PE",     "Windows PE (alias MZ)"),
    # Java class file
    (b"\xca\xfe\xba\xbe", "Java",   "Java .class bytecode"),
    # WebAssembly
    (b"\x00asm",           "WASM",   "WebAssembly binary"),
    # Python compiled bytecode (.pyc) — magic varies by version
    (b"\x0d\x0d\x0a\x0a", "PYC",   "Python bytecode (3.8+)"),
]

# Máximo de bytes do início do conteúdo para verificar magic
_MAGIC_SCAN_BYTES = 8

# ---------------------------------------------------------------------------
# Shellcode heuristics — sequências comuns de NOP sled / syscall
# ---------------------------------------------------------------------------

# NOP sled: 16+ bytes consecutivos de 0x90 (x86 NOP)
_NOP_SLED_MIN = 16
_NOP_PATTERN = re.compile(rb"\x90{" + str(_NOP_SLED_MIN).encode() + rb",}")

# Common shellcode patterns: int 0x80 (Linux x86 syscall), syscall (x64), sysenter
_SYSCALL_PATTERNS = [
    rb"\xcd\x80",       # int 0x80 (Linux x86)
    rb"\x0f\x05",       # syscall (x86-64)
    rb"\x0f\x34",       # sysenter
]

# Windows shellcode: LoadLibraryA / WinExec pattern (mov eax, hash)
_WIN_SHELLCODE_PATTERNS = [
    rb"\x64\x8b\x52\x30",   # PEB traversal (common in Windows shellcode)
    rb"\x31\xc9\x64\x8b",   # PEB traversal variant
]

_ALL_SHELLCODE_PATTERNS = [re.compile(p) for p in _SYSCALL_PATTERNS + _WIN_SHELLCODE_PATTERNS]

# ---------------------------------------------------------------------------
# Base64-encoded executable detection
# ---------------------------------------------------------------------------

# Minimum length of base64 blob to consider suspicious (avoids false positives on UUIDs etc.)
_B64_MIN_LEN = 200

# Regex for contiguous base64-ish block
_B64_BLOB_PATTERN = re.compile(
    rb"(?:[A-Za-z0-9+/]{4}){" + str(_B64_MIN_LEN // 4).encode() + rb",}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?"
)

# PowerShell / bash encoded command markers (text-level detection)
_ENCODED_CMD_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"-EncodedCommand\s+[A-Za-z0-9+/=]{20,}", re.IGNORECASE),
    re.compile(r"echo\s+[A-Za-z0-9+/=]{40,}\s*\|\s*base64\s+-d", re.IGNORECASE),
    re.compile(r"base64\s+--?decode", re.IGNORECASE),
    re.compile(r"eval\s*\(\s*base64_decode\s*\(", re.IGNORECASE),
    re.compile(r"\$\([A-Za-z0-9+/=]{40,}\)", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BinaryFinding:
    kind: str        # "magic", "shellcode", "base64_exec", "encoded_cmd"
    detail: str      # human-readable description
    severity: str    # "high" | "medium" | "low"
    offset: int = 0  # byte offset in content where finding was detected


@dataclass
class ScanResult:
    is_binary: bool = False
    findings: list[BinaryFinding] = field(default_factory=list)
    truncated: bool = False  # True if content was too large to scan fully

    @property
    def is_suspicious(self) -> bool:
        return bool(self.findings)

    def summary(self) -> str:
        if not self.findings:
            return "clean"
        parts = [f"{f.severity.upper()} [{f.kind}] {f.detail}" for f in self.findings]
        return "; ".join(parts)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class BinaryScanner:
    """Scans bytes or text for executable/suspicious content.

    Usage::

        scanner = BinaryScanner()
        result = scanner.scan_bytes(data)
        if result.is_suspicious:
            print(result.summary())
    """

    def __init__(
        self,
        check_magic: bool = True,
        check_shellcode: bool = True,
        check_base64: bool = True,
        check_encoded_cmd: bool = True,
        max_scan_bytes: int = 5 * 1024 * 1024,  # 5 MB
    ):
        self.check_magic = check_magic
        self.check_shellcode = check_shellcode
        self.check_base64 = check_base64
        self.check_encoded_cmd = check_encoded_cmd
        self.max_scan_bytes = max_scan_bytes

    def scan_bytes(self, data: bytes) -> ScanResult:
        result = ScanResult()
        if not data:
            return result

        truncated = len(data) > self.max_scan_bytes
        if truncated:
            data = data[: self.max_scan_bytes]
            result.truncated = True

        if self.check_magic:
            self._scan_magic(data, result)

        if not result.is_binary:
            # Shellcode and base64 checks only make sense on non-obvious executables
            # (or when we're checking text that might embed shellcode)
            if self.check_shellcode:
                self._scan_shellcode(data, result)
            if self.check_base64:
                self._scan_base64_exec(data, result)

        return result

    def scan_text(self, text: str) -> ScanResult:
        result = ScanResult()
        if not text:
            return result

        # Check encoded command patterns (text-level)
        if self.check_encoded_cmd:
            self._scan_encoded_cmd(text, result)

        # Also scan as bytes for embedded binary/base64
        try:
            data = text.encode("utf-8", errors="replace")
        except Exception:
            return result
        truncated = len(data) > self.max_scan_bytes
        if truncated:
            data = data[: self.max_scan_bytes]
            result.truncated = True

        if self.check_base64:
            self._scan_base64_exec(data, result)

        return result

    # ------------------------------------------------------------------
    # Private scan methods
    # ------------------------------------------------------------------

    def _scan_magic(self, data: bytes, result: ScanResult) -> None:
        header = data[:_MAGIC_SCAN_BYTES]
        for magic, fmt, desc in _MAGIC_SIGNATURES:
            if header.startswith(magic):
                result.is_binary = True
                result.findings.append(BinaryFinding(
                    kind="magic",
                    detail=f"{fmt} binary detected ({desc})",
                    severity="high",
                    offset=0,
                ))
                return  # One magic match is enough

        # Heuristic: high ratio of non-printable bytes → likely binary
        non_printable = sum(1 for b in data[:512] if b < 0x09 or (0x0e <= b <= 0x1f))
        if len(data) >= 64 and non_printable / min(len(data), 512) > 0.30:
            result.is_binary = True
            result.findings.append(BinaryFinding(
                kind="magic",
                detail=f"high non-printable byte ratio ({non_printable}/{min(len(data),512)}) — likely binary",
                severity="medium",
                offset=0,
            ))

    def _scan_shellcode(self, data: bytes, result: ScanResult) -> None:
        # NOP sled
        m = _NOP_PATTERN.search(data)
        if m:
            result.findings.append(BinaryFinding(
                kind="shellcode",
                detail=f"NOP sled detected ({len(m.group())} bytes) at offset {m.start()}",
                severity="high",
                offset=m.start(),
            ))

        # Syscall / PEB patterns
        for pattern in _ALL_SHELLCODE_PATTERNS:
            m = pattern.search(data)
            if m:
                result.findings.append(BinaryFinding(
                    kind="shellcode",
                    detail=f"shellcode pattern {pattern.pattern!r} at offset {m.start()}",
                    severity="high",
                    offset=m.start(),
                ))
                break  # Report first hit only to avoid noise

    def _scan_base64_exec(self, data: bytes, result: ScanResult) -> None:
        for m in _B64_BLOB_PATTERN.finditer(data):
            blob = m.group()
            try:
                decoded = base64.b64decode(blob + b"==")  # tolerate missing padding
            except (binascii.Error, ValueError):
                continue

            # Check if decoded bytes look like a binary
            sub = ScanResult()
            self._scan_magic(decoded, sub)
            if sub.is_binary:
                result.findings.append(BinaryFinding(
                    kind="base64_exec",
                    detail=f"base64-encoded executable detected (decoded {len(decoded)} bytes) at offset {m.start()}",
                    severity="high",
                    offset=m.start(),
                ))
                break  # First hit is sufficient

    def _scan_encoded_cmd(self, text: str, result: ScanResult) -> None:
        for pattern in _ENCODED_CMD_PATTERNS:
            m = pattern.search(text)
            if m:
                result.findings.append(BinaryFinding(
                    kind="encoded_cmd",
                    detail=f"encoded/obfuscated command pattern: {m.group()[:80]!r}",
                    severity="medium",
                    offset=m.start(),
                ))


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_default_scanner: BinaryScanner | None = None


def get_scanner() -> BinaryScanner:
    global _default_scanner
    if _default_scanner is None:
        _default_scanner = BinaryScanner()
    return _default_scanner


def scan(data: bytes | str) -> ScanResult:
    """Convenience wrapper — auto-detects bytes vs str."""
    scanner = get_scanner()
    if isinstance(data, bytes):
        return scanner.scan_bytes(data)
    return scanner.scan_text(data)

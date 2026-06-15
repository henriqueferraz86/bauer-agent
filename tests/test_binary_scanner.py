"""Tests for bauer.binary_scanner."""

from __future__ import annotations

import base64
import pytest

from bauer.binary_scanner import (
    BinaryScanner,
    ScanResult,
    BinaryFinding,
    scan,
    get_scanner,
)


class TestMagicBytesDetection:
    def test_elf_binary(self):
        data = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 56
        result = BinaryScanner().scan_bytes(data)
        assert result.is_binary
        assert any(f.kind == "magic" for f in result.findings)
        assert any("ELF" in f.detail for f in result.findings)

    def test_pe_binary(self):
        data = b"MZ\x90\x00" + b"\x00" * 60
        result = BinaryScanner().scan_bytes(data)
        assert result.is_binary
        assert any("PE" in f.detail for f in result.findings)

    def test_macho_64(self):
        data = b"\xfe\xed\xfa\xcf" + b"\x00" * 20
        result = BinaryScanner().scan_bytes(data)
        assert result.is_binary

    def test_wasm(self):
        data = b"\x00asm\x01\x00\x00\x00"
        result = BinaryScanner().scan_bytes(data)
        assert result.is_binary

    def test_clean_text(self):
        data = b"Hello, world! This is plain text.\n"
        result = BinaryScanner().scan_bytes(data)
        assert not result.is_binary

    def test_python_source(self):
        data = b"#!/usr/bin/env python3\nprint('hello')\n"
        result = BinaryScanner().scan_bytes(data)
        # Shebang is flagged as "Script" but not necessarily blocked
        assert any(f.kind == "magic" for f in result.findings) or not result.is_binary

    def test_high_nonprintable_ratio(self):
        # 50% non-printable bytes → flagged as binary
        data = bytes([0x00, 0x41] * 256)
        result = BinaryScanner().scan_bytes(data)
        assert result.is_binary

    def test_low_nonprintable_ratio(self):
        # Normal text with a few control chars — not flagged
        data = b"Normal text\n\t with tabs and newlines.\n" * 10
        result = BinaryScanner().scan_bytes(data)
        assert not result.is_binary

    def test_empty_bytes(self):
        result = BinaryScanner().scan_bytes(b"")
        assert not result.is_binary
        assert not result.is_suspicious


class TestShellcodeDetection:
    def test_nop_sled(self):
        data = b"\x90" * 24 + b"\xcd\x80"
        result = BinaryScanner(check_magic=False).scan_bytes(data)
        assert any(f.kind == "shellcode" for f in result.findings)
        sled = next(f for f in result.findings if "NOP" in f.detail)
        assert "24" in sled.detail

    def test_short_nop_not_flagged(self):
        data = b"\x90" * 8 + b"AAAA"
        result = BinaryScanner(check_magic=False, check_shellcode=True).scan_bytes(data)
        nops = [f for f in result.findings if "NOP" in f.detail]
        assert not nops  # below threshold of 16

    def test_linux_syscall_int80(self):
        data = b"AAAA" + b"\xcd\x80" + b"BBBB"
        result = BinaryScanner(check_magic=False).scan_bytes(data)
        assert any(f.kind == "shellcode" for f in result.findings)

    def test_x64_syscall(self):
        data = b"CCCC" + b"\x0f\x05" + b"DDDD"
        result = BinaryScanner(check_magic=False).scan_bytes(data)
        assert any(f.kind == "shellcode" for f in result.findings)

    def test_peb_traversal(self):
        data = b"\x64\x8b\x52\x30" * 2
        result = BinaryScanner(check_magic=False).scan_bytes(data)
        assert any(f.kind == "shellcode" for f in result.findings)

    def test_severity_is_high(self):
        data = b"\x90" * 20
        result = BinaryScanner(check_magic=False).scan_bytes(data)
        shellcode_findings = [f for f in result.findings if f.kind == "shellcode"]
        assert all(f.severity == "high" for f in shellcode_findings)


class TestBase64ExecDetection:
    def _make_b64_elf(self) -> str:
        elf_header = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 248
        return base64.b64encode(elf_header).decode()

    def test_base64_elf_in_text(self):
        b64 = self._make_b64_elf()
        result = BinaryScanner(check_magic=True, check_base64=True).scan_text(
            f"Here is some data: {b64}"
        )
        assert any(f.kind == "base64_exec" for f in result.findings)

    def test_short_base64_ignored(self):
        tiny = base64.b64encode(b"hello").decode()
        result = BinaryScanner(check_base64=True).scan_text(f"data={tiny}")
        assert not any(f.kind == "base64_exec" for f in result.findings)

    def test_base64_clean_data_ok(self):
        clean = base64.b64encode(b"plain text " * 20).decode()
        result = BinaryScanner(check_base64=True).scan_text(clean)
        # Not an executable binary — should not be flagged as base64_exec
        assert not any(f.kind == "base64_exec" for f in result.findings)


class TestEncodedCommandDetection:
    def test_powershell_encoded_command(self):
        cmd = "-EncodedCommand " + "A" * 60
        result = BinaryScanner(check_encoded_cmd=True).scan_text(cmd)
        assert any(f.kind == "encoded_cmd" for f in result.findings)

    def test_bash_base64_decode(self):
        cmd = "echo AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA== | base64 -d | sh"
        result = BinaryScanner(check_encoded_cmd=True).scan_text(cmd)
        assert any(f.kind == "encoded_cmd" for f in result.findings)

    def test_php_eval_base64(self):
        cmd = "eval(base64_decode('AAAA'));"
        result = BinaryScanner(check_encoded_cmd=True).scan_text(cmd)
        assert any(f.kind == "encoded_cmd" for f in result.findings)

    def test_normal_command_ok(self):
        cmd = "ls -la /tmp && echo done"
        result = BinaryScanner(check_encoded_cmd=True).scan_text(cmd)
        assert not any(f.kind == "encoded_cmd" for f in result.findings)

    def test_severity_is_medium(self):
        cmd = "-EncodedCommand " + "B" * 60
        result = BinaryScanner(check_encoded_cmd=True).scan_text(cmd)
        findings = [f for f in result.findings if f.kind == "encoded_cmd"]
        assert all(f.severity == "medium" for f in findings)


class TestScanResult:
    def test_is_suspicious_false_when_clean(self):
        r = ScanResult()
        assert not r.is_suspicious

    def test_is_suspicious_true_with_finding(self):
        r = ScanResult(findings=[BinaryFinding(kind="magic", detail="ELF", severity="high")])
        assert r.is_suspicious

    def test_summary_clean(self):
        assert ScanResult().summary() == "clean"

    def test_summary_with_findings(self):
        r = ScanResult(findings=[
            BinaryFinding(kind="shellcode", detail="NOP sled", severity="high"),
            BinaryFinding(kind="magic", detail="ELF binary", severity="high"),
        ])
        s = r.summary()
        assert "shellcode" in s
        assert "HIGH" in s


class TestTruncation:
    def test_large_data_truncated(self):
        # 1 byte over the limit, clean data
        scanner = BinaryScanner(max_scan_bytes=100)
        data = b"A" * 101
        result = scanner.scan_bytes(data)
        assert result.truncated

    def test_small_data_not_truncated(self):
        scanner = BinaryScanner(max_scan_bytes=100)
        data = b"A" * 50
        result = scanner.scan_bytes(data)
        assert not result.truncated


class TestConvenienceScan:
    def test_scan_bytes(self):
        result = scan(b"\x7fELF" + b"\x00" * 20)
        assert result.is_binary

    def test_scan_text(self):
        result = scan("Hello, clean world!")
        assert not result.is_suspicious

    def test_singleton_scanner(self):
        s1 = get_scanner()
        s2 = get_scanner()
        assert s1 is s2


class TestDisabledChecks:
    def test_disable_magic(self):
        data = b"\x7fELF" + b"\x00" * 20
        result = BinaryScanner(check_magic=False).scan_bytes(data)
        assert not result.is_binary

    def test_disable_shellcode(self):
        data = b"\x90" * 24
        result = BinaryScanner(check_magic=False, check_shellcode=False).scan_bytes(data)
        assert not any(f.kind == "shellcode" for f in result.findings)

    def test_disable_encoded_cmd(self):
        result = BinaryScanner(check_encoded_cmd=False).scan_text(
            "-EncodedCommand " + "X" * 60
        )
        assert not any(f.kind == "encoded_cmd" for f in result.findings)

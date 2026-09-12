"""Гвард критерия приёмки №8 (docs/PLAN.md): установка по умолчанию не делает
сетевых вызовов и не читает credentials.

Область — `src/`, `bin/`, `scripts/`: то, что реально ставит `install.sh`
(см. docs/PLAN.md, «Жёсткие ограничения» §1–2). Опциональный сетевой fallback
из docs/superpowers/specs/2026-09-07-network-fallback-design.md — отдельный,
не входящий в install.sh каталог с собственным guard'ом; сюда не входит.

Раньше критерий держался на разовом ручном грепе («guard Task 6» из
.superpowers/sdd/PLAN/progress.md), которого на диске не существовало —
это и есть долг #31.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCANNED_DIRS = ("src", "bin", "scripts")
_SCANNED_SUFFIXES = {".py", ".sh"}

_NETWORK_PATTERNS = {
    "python network library": re.compile(
        r"\b(?:import|from)\s+(?:urllib|http\.client|requests|socket|httpx|aiohttp)\b"
    ),
    "shell network tool": re.compile(r"\b(?:curl|wget|nc|ssh|scp)\b"),
}

_CREDENTIAL_PATTERNS = {
    # Без \b намеренно: реальные утечки чаще выглядят как OAUTH_TOKEN или
    # access_token — составные SNAKE_CASE/camelCase имена, где подчёркивание
    # или смена регистра не создают границу слова для \b, и он их бы пропустил.
    "credentials file": re.compile(r"\.credentials\.json|credentials?", re.IGNORECASE),
    "oauth token / api key": re.compile(r"token|api[_-]?key|authorization|bearer", re.IGNORECASE),
}

# Каждая запись — конкретная строка в конкретном файле, не файл целиком: узкое
# разрешение, которое перестаёт действовать при малейшей правке строки. Все три
# принадлежат profiles.discover_profiles(), которая намеренно ПРОВЕРЯЕТ наличие
# .credentials.json (os.path.exists), но никогда не открывает файл — это и есть
# контракт «ноль доступа к секретам», а не его нарушение.
_ALLOWED_CREDENTIAL_MENTIONS: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "src/claude_usage_indicator/profiles.py",
            "Признак — существование `.credentials.json` внутри каталога. Файл именно",
        ),
        (
            "src/claude_usage_indicator/profiles.py",
            'if default_dir.is_dir() and (default_dir / ".credentials.json").exists():',
        ),
        (
            "src/claude_usage_indicator/profiles.py",
            'if not candidate.is_dir() or not (candidate / ".credentials.json").exists():',
        ),
    }
)


def _iter_scanned_files() -> list[Path]:
    files = []
    for dirname in _SCANNED_DIRS:
        base = _REPO_ROOT / dirname
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix in _SCANNED_SUFFIXES and "__pycache__" not in path.parts:
                files.append(path)
    return files


def _scan(patterns: dict[str, re.Pattern[str]]) -> tuple[list[str], set[tuple[str, str]]]:
    """Возвращает (находки вне allowlist, использованные разрешения из allowlist)."""
    violations: list[str] = []
    allowed_hits: set[tuple[str, str]] = set()
    for path in _iter_scanned_files():
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            for name, pattern in patterns.items():
                if not pattern.search(line):
                    continue
                key = (rel, stripped)
                if key in _ALLOWED_CREDENTIAL_MENTIONS:
                    allowed_hits.add(key)
                    continue
                violations.append(f"{rel}:{lineno}: [{name}] {stripped}")
    return violations, allowed_hits


class NetworkGuardTests(unittest.TestCase):
    def test_default_install_makes_no_network_calls(self):
        violations, _ = _scan(_NETWORK_PATTERNS)
        self.assertEqual(violations, [], "found network calls in default install scope")

    def test_default_install_does_not_touch_credentials(self):
        violations, allowed_hits = _scan(_CREDENTIAL_PATTERNS)
        self.assertEqual(violations, [], "found credentials/token access in default install scope")
        # Allowlist должен реально попадать под сканируемые паттерны здесь и сейчас:
        # исключение на строку, которая больше не совпадает (файл поправили, а
        # запись забыли), должно проваливать тест, а не молча становиться мёртвым
        # грузом — иначе гвард однажды окажется зелёным просто потому, что перестал
        # что-либо находить.
        self.assertEqual(
            allowed_hits,
            set(_ALLOWED_CREDENTIAL_MENTIONS),
            "allowlist has a stale entry that no longer matches any scanned line",
        )

    def test_scanned_scope_is_not_empty(self):
        # Пустой список файлов сделал бы оба теста выше зелёными без единой
        # проверенной строки — защита от гварда, который «работает», потому что
        # ему нечего сканировать.
        self.assertGreater(len(_iter_scanned_files()), 0)


if __name__ == "__main__":
    unittest.main()

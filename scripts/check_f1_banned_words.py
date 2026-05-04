"""F-1 banned-word grep. Runs as pre-commit hook + CI.

Per spec §10 (the audit checklist) and Phase D C4: the canonical regex source
is `_workspace/f1-banned-words.regex` (committed to this repo). All three users
(this hook, DevOps CI, Tech Writer README/landing CI) read from that file.

Targets (per spec §10 + the regex file's own header):
- app/static/**          (every file)
- openapi.json           (post-generation; CI fetches it)
- README.md              (config block + body)
- Route decorator strings extracted via ast.parse from app/api/**/*.py

Explicit exemptions (spec §10):
- Bodies of app/api/**/*.py outside route-decorator string args
  (Python comments / log strings / identifiers can reference these tokens
  for technical reasons).
- CHANGELOG.md (allowlist for upstream issue references).
- _workspace/** (planning notes, not user-facing).
- tests/**.
- Inline `# noqa: f1` comments at allowed callsites (Phase D A1.1).

Exit code 1 on any hit (CI fails).
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGEX_FILE = REPO_ROOT / "_workspace" / "f1-banned-words.regex"


def load_canonical_regex() -> re.Pattern[str]:
    """Read patterns from the canonical regex file (C4). Skip blank/comment lines."""
    patterns: list[str] = []
    for raw_line in REGEX_FILE.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(f"(?:{line})")
    if not patterns:
        raise RuntimeError(f"no patterns found in {REGEX_FILE}")
    joined = "|".join(patterns)
    return re.compile(joined, re.IGNORECASE)


def is_exempt_line(line: str) -> bool:
    """Phase D A1.1: inline `# noqa: f1` exempts the line."""
    return "# noqa: f1" in line


def scan_file(path: Path, pat: re.Pattern[str]) -> list[tuple[int, str, str]]:
    """Return list of (line_no, matched_token, line_text) hits."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if is_exempt_line(line):
            continue
        for m in pat.finditer(line):
            hits.append((i, m.group(0), line.strip()))
    return hits


def scan_route_decorators(path: Path, pat: re.Pattern[str]) -> list[tuple[int, str, str]]:
    """Extract `summary=` and `description=` string args from FastAPI route
    decorators in `path`, scan only those strings.

    Per spec §10 grep-targets: 'All FastAPI route decorator summary= and
    description= arg values, extracted from app/api/**/*.py via ast.parse'.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    hits: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            for kw in dec.keywords:
                if kw.arg in ("summary", "description") and isinstance(kw.value, ast.Constant):
                    s = kw.value.value
                    if isinstance(s, str):
                        for m in pat.finditer(s):
                            hits.append((kw.value.lineno, m.group(0), s.strip()[:120]))
    return hits


def main() -> int:
    pat = load_canonical_regex()
    all_hits: list[tuple[Path, int, str, str]] = []

    # 1. app/static/** — every file.
    static_dir = REPO_ROOT / "app" / "static"
    if static_dir.exists():
        for p in static_dir.rglob("*"):
            if p.is_file():
                for line_no, tok, line in scan_file(p, pat):
                    all_hits.append((p, line_no, tok, line))

    # 2. README.md (config block + body).
    readme = REPO_ROOT / "README.md"
    if readme.is_file():
        for line_no, tok, line in scan_file(readme, pat):
            all_hits.append((readme, line_no, tok, line))

    # 3. openapi.json (CI generates this; if present in repo, scan).
    openapi = REPO_ROOT / "openapi.json"
    if openapi.is_file():
        for line_no, tok, line in scan_file(openapi, pat):
            all_hits.append((openapi, line_no, tok, line))

    # 4. Route decorator strings under app/api/**/*.py.
    api_dir = REPO_ROOT / "app" / "api"
    if api_dir.exists():
        for p in api_dir.rglob("*.py"):
            for line_no, tok, line in scan_route_decorators(p, pat):
                all_hits.append((p, line_no, tok, line))

    # 5. The FastAPI app's title/description (in app/main.py) is also user-facing.
    main_py = REPO_ROOT / "app" / "main.py"
    if main_py.is_file():
        for line_no, tok, line in scan_route_decorators(main_py, pat):
            all_hits.append((main_py, line_no, tok, line))
        # Also scan top-level FastAPI(...) call's `description=` kwarg.
        try:
            tree = ast.parse(main_py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FastAPI":
                    for kw in node.keywords:
                        if kw.arg in ("title", "description") and isinstance(kw.value, ast.Constant):
                            s = kw.value.value
                            if isinstance(s, str):
                                for m in pat.finditer(s):
                                    all_hits.append((main_py, kw.value.lineno, m.group(0), s.strip()[:120]))
        except (OSError, SyntaxError):
            pass

    if all_hits:
        print("F-1 banned-word check FAILED:")
        for p, line_no, tok, line in all_hits:
            rel = p.relative_to(REPO_ROOT)
            print(f"  {rel}:{line_no}  matched={tok!r}  line={line[:120]!r}")
        return 1
    print("F-1 banned-word check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Fails if any function in the code is missing from the two code guides.

Both docs must mention every function on a line that also names its file, e.g.
    `ChatService.complete()` · `backend/app/services/chat_service.py`

How names are written:
- top-level function:              `create_app`
- method:                          `ChatService.complete`
- function defined inside another: `lifespan` (just its own name)

Run alone: uv run pytest tests/test_docs_coverage.py -q
"""

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCS = {
    "code map": REPO / "docs" / "WORKFLOW_CODE_MAP.md",
    "function guide": REPO / "docs" / "FUNCTION_GUIDE.md",
}
PY_DIRS = [REPO / "backend" / "app", REPO / "backend" / "tests", REPO / "backend" / "migrations"]
TS_DIRS = [REPO / "frontend" / "app", REPO / "frontend" / "components", REPO / "frontend" / "lib"]

# TypeScript: named functions, arrow functions / hooks assigned to a const, and classes.
TS_PATTERNS = [
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)", re.M),
    # `const x = (a, b) => ...`, `const x = useCallback(async () => ...)`, `const x = async <T,>(...): R =>`
    re.compile(
        r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:useCallback\()?\s*(?:async\s*)?(?:<[^>]*>\s*)?"
        r"\([^()]*(?:\([^()]*\)[^()]*)*\)\s*(?::\s*[^=]+?)?\s*=>",
        re.M,
    ),
    re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.M),
]


def _python_functions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names: list[str] = []

    def visit(node: ast.AST, class_name: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(f"{class_name}.{child.name}" if class_name else child.name)
                visit(child, None)  # nested functions are listed by their own name

    visit(tree, None)
    return names


def _all_functions() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for base in PY_DIRS:
        for path in sorted(base.rglob("*.py")) if base.exists() else []:
            rel = path.relative_to(REPO).as_posix()
            found += [(rel, name) for name in _python_functions(path)]
    for base in TS_DIRS:
        for path in sorted([*base.rglob("*.ts"), *base.rglob("*.tsx")]):
            rel = path.relative_to(REPO).as_posix()
            text = path.read_text()
            names = {m.group(1) for pattern in TS_PATTERNS for m in pattern.finditer(text)}
            found += [(rel, name) for name in sorted(names)]
    return found


FUNCTIONS = _all_functions()


def _mentioned(doc_lines: list[str], file: str, name: str) -> bool:
    pattern = re.compile(rf"`{re.escape(name)}(\(\))?`")
    return any(file in line and pattern.search(line) for line in doc_lines)


@pytest.mark.parametrize("doc", list(DOCS))
def test_every_function_is_documented(doc: str):
    lines = DOCS[doc].read_text().splitlines()
    missing = [f"{file} :: {name}" for file, name in FUNCTIONS if not _mentioned(lines, file, name)]
    assert not missing, f"{len(missing)} functions missing from the {doc}:\n" + "\n".join(missing)


def test_scanner_finds_functions():
    names = {name for _, name in FUNCTIONS}
    assert {"create_app", "ChatService.complete", "lifespan", "streamChat", "Chat"} <= names

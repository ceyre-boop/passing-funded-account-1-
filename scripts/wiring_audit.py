#!/usr/bin/env python3
"""wiring_audit.py — detector for the "built, tested, never wired" failure class.

Plans/THE_BIG_PLAN.md documents four major instances (D1-D4) of components that
exist, pass their own tests, and are never actually connected to anything that
runs. This script makes that class of defect MECHANICAL and DETECTABLE rather
than something someone has to notice during a manual audit.

Five categories, all via static (ast-based) analysis — modules under
daytrade/, sovereign/, execution/, backtester/, scripts/ are NEVER imported by
this script to inspect them; several are unimportable by design (broken
imports are themselves a category we detect) and importing daytrade/* has
real side effects (bar caches, decision logs, etc.).

1. ORPHANS            — module with no importer anywhere outside test files.
2. IMPORTED_NOT_CALLED — module X imports project-module Y, never calls
                          anything from it (CLAUDE.md rule 7, made mechanical).
3. NO_SUPPLIER         — a dataclass field defaulting to None is READ
                          somewhere in production code but never WRITTEN a
                          non-None value anywhere in production code.
4. BROKEN_IMPORT       — a module whose import statements reference a name
                          that resolves to nothing on disk, in the stdlib, or
                          in the installed site-packages (e.g. kelly_engine.py
                          -> `layer2`, which does not exist anywhere).
5. DEAD_ARTIFACT        — a literal data/... path written somewhere in
                          production code but never read anywhere.

Known-and-intentional disconnects are allowlisted in
data/agent/wiring_allowlist.yaml. Every allowlist entry REQUIRES a non-empty
`reason` string — an entry with no reason is itself an audit error. Anything
NOT allowlisted that this script finds is a live finding; scripts/test_wiring_audit.py
turns "new disconnect appears" into a failing test.

Usage:
    python3 scripts/wiring_audit.py                 # human-readable report
    python3 scripts/wiring_audit.py --json           # machine-readable report
"""
from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_DIRS = ["daytrade", "sovereign", "execution", "backtester", "scripts"]
ALLOWLIST_PATH = REPO_ROOT / "data" / "agent" / "wiring_allowlist.yaml"

SHELL_CALLERS = [
    REPO_ROOT / "daytrade" / "operator_tick.sh",
    REPO_ROOT / "daytrade" / "dashboard_publish.sh",
]

# Modules dynamically loaded via importlib.import_module(f"...{name}") whose
# exact target name is only known at runtime. Each entry: (caller module,
# resolved package prefix the dynamic import draws from). Found by grepping
# for import_module( calls that use an f-string / .format() with a static
# prefix — see _find_dynamic_import_prefixes().
DYNAMIC_IMPORT_MARKERS = ("import_module(",)

STDLIB_NAMES = set(getattr(sys, "stdlib_module_names", ())) | {
    "__future__",
}


# --------------------------------------------------------------------------
# File discovery
# --------------------------------------------------------------------------

def is_test_file(path: Path) -> bool:
    name = path.name
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def iter_py_files() -> Iterable[Path]:
    for d in TARGET_DIRS:
        root = REPO_ROOT / d
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            yield p


def module_name_for(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def rel_str(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


# --------------------------------------------------------------------------
# AST parsing helpers
# --------------------------------------------------------------------------

@dataclass
class ParsedModule:
    path: Path
    module_name: str
    is_test: bool
    tree: Optional[ast.Module]
    parse_error: Optional[str] = None
    source: str = ""


def parse_all() -> dict[str, ParsedModule]:
    modules: dict[str, ParsedModule] = {}
    for path in iter_py_files():
        mod_name = module_name_for(path)
        src = path.read_text(errors="replace")
        try:
            tree = ast.parse(src, filename=str(path))
            err = None
        except SyntaxError as e:  # pragma: no cover — repo is expected to parse cleanly
            tree = None
            err = str(e)
        modules[mod_name] = ParsedModule(
            path=path,
            module_name=mod_name,
            is_test=is_test_file(path),
            tree=tree,
            parse_error=err,
            source=src,
        )
    return modules


def resolve_relative(current_module: str, is_package: bool, level: int, module: Optional[str]) -> str:
    """Mirror importlib's relative-import resolution rule, purely on strings."""
    base_parts = current_module.split(".") if current_module else []
    if not is_package:
        base_parts = base_parts[:-1]
    if level > 1:
        cut = level - 1
        base_parts = base_parts[:-cut] if cut <= len(base_parts) else []
    if module:
        return ".".join(base_parts + module.split("."))
    return ".".join(base_parts)


@dataclass
class ImportEdge:
    # local name bound in the importing module's namespace
    local_name: str
    # fully resolved dotted module path this binding ultimately refers to
    target_module: str
    # if this is a `from X import symbol`, the symbol name (None for whole-module import)
    symbol: Optional[str]
    star: bool = False


_SYSPATH_ROOT_DIR_RE = re.compile(r'sys\.path\.insert\(\s*0\s*,\s*str\(\s*ROOT\s*/\s*["\'](\w+)["\']\s*\)')


def _extra_syspath_prefixes(source: str) -> list[str]:
    """A number of scripts explicitly add another repo directory to sys.path
    (`sys.path.insert(0, str(ROOT / "daytrade"))`), meaning a bare `import
    bars` from a scripts/*.py file resolves to daytrade.bars, not a sibling
    of scripts/. Detected per-file so the exemption only applies where the
    file actually does this."""
    return _SYSPATH_ROOT_DIR_RE.findall(source)


def _resolve_sibling(name: str, all_module_names: set[str], current_package: str,
                      extra_prefixes: tuple[str, ...] = ()) -> str:
    """Many scripts in this repo run with their own directory (or another
    repo directory, added explicitly via sys.path.insert) on sys.path (see
    daytrade/operator_tick.sh's `sys.path.insert(0, '.')`), so a bare
    `import bars` from within daytrade/ really means daytrade.bars. Prefer a
    same-package (or explicitly sys.path'd) sibling match when the bare name
    isn't itself a top-level repo module."""
    if name in all_module_names:
        return name
    if current_package:
        sib = f"{current_package}.{name}"
        if sib in all_module_names:
            return sib
    for prefix in extra_prefixes:
        sib = f"{prefix}.{name}"
        if sib in all_module_names:
            return sib
    return name


def module_imports(pm: ParsedModule, all_module_names: set[str]) -> list[ImportEdge]:
    """All import statements in a module, resolved to absolute dotted paths."""
    if pm.tree is None:
        return []
    is_package = pm.path.name == "__init__.py"
    current_package = ".".join(pm.module_name.split(".")[:-1]) if not is_package else pm.module_name
    extra_prefixes = tuple(_extra_syspath_prefixes(pm.source))
    edges: list[ImportEdge] = []
    for node in ast.walk(pm.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _resolve_sibling(alias.name, all_module_names, current_package, extra_prefixes)
                local = alias.asname or alias.name.split(".")[0]
                edges.append(ImportEdge(local_name=local, target_module=target, symbol=None))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                base = resolve_relative(pm.module_name, is_package, node.level, node.module)
            else:
                base = node.module or ""
                base = _resolve_sibling(base, all_module_names, current_package, extra_prefixes)
            for alias in node.names:
                if alias.name == "*":
                    edges.append(ImportEdge(local_name="*", target_module=base, symbol=None, star=True))
                    continue
                local = alias.asname or alias.name
                # is `alias.name` actually a submodule of `base`? (from pkg import submod)
                candidate_module = f"{base}.{alias.name}" if base else alias.name
                if candidate_module in all_module_names:
                    edges.append(ImportEdge(local_name=local, target_module=candidate_module, symbol=None))
                else:
                    edges.append(ImportEdge(local_name=local, target_module=base, symbol=alias.name))
    return edges


# --------------------------------------------------------------------------
# Category 4: BROKEN_IMPORT — static resolvability check, no execution
# --------------------------------------------------------------------------

def _site_package_roots() -> list[Path]:
    roots = []
    for p in sys.path:
        if not p:
            continue
        pp = Path(p)
        if pp.is_dir() and ("site-packages" in pp.parts or pp.name.endswith(".zip") is False):
            roots.append(pp)
    return roots


_SITE_ROOTS = [p for p in _site_package_roots() if p.is_dir()]

# distribution name -> importable top-level name, for the handful of
# requirements.txt entries where they differ.
_DIST_TO_IMPORT_ALIAS = {
    "pyyaml": "yaml",
    "python-dotenv": "dotenv",
    "scikit-learn": "sklearn",
    "pyarrow": "pyarrow",
}


def _declared_requirements() -> set[str]:
    """Package names declared in requirements*.txt — legitimate dependencies
    that may simply not be pip-installed in the environment this audit
    happens to run in (e.g. an optional/heavy dep like numba/hmmlearn)."""
    names: set[str] = set()
    for req_file in REPO_ROOT.glob("requirements*.txt"):
        for line in req_file.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            pkg = re.split(r"[<>=!\[;~]", line, maxsplit=1)[0].strip().lower()
            if not pkg:
                continue
            names.add(pkg)
            names.add(_DIST_TO_IMPORT_ALIAS.get(pkg, pkg).replace("-", "_"))
    return names


_DECLARED_REQUIREMENTS = _declared_requirements()


def _top_level_resolvable(name: str, all_module_names: set[str], current_package: str = "",
                           extra_prefixes: tuple[str, ...] = ()) -> bool:
    """Can the first component of a dotted import name be found anywhere
    (repo, stdlib, declared requirements, or installed site-packages)
    without executing anything?"""
    if not name:
        return True
    top = name.split(".")[0]
    if top in STDLIB_NAMES:
        return True
    if top.lower() in _DECLARED_REQUIREMENTS:
        return True
    # repo-local: any known module whose dotted path starts with `top.` or equals `top`
    if any(m == top or m.startswith(top + ".") for m in all_module_names):
        return True
    # sibling-module resolution: many scripts here run with their own
    # directory (or another repo directory explicitly sys.path'd) on
    # sys.path (see daytrade/operator_tick.sh's `sys.path.insert(0, '.')`),
    # so `import bars` from within daytrade/ means daytrade.bars, not a
    # top-level `bars` package.
    if current_package:
        sib = f"{current_package}.{top}"
        if sib in all_module_names:
            return True
    for prefix in extra_prefixes:
        sib = f"{prefix}.{top}"
        if sib in all_module_names:
            return True
    if (REPO_ROOT / top).is_dir() or (REPO_ROOT / f"{top}.py").is_file():
        return True
    for root in _SITE_ROOTS:
        if (root / top).exists():
            return True
        if (root / f"{top}.py").exists():
            return True
        # dist-info / egg-link naming is looser; also accept a case-insensitive
        # match and common `-` / `_` swaps.
        alt = top.replace("_", "-")
        if any(root.glob(f"{alt}*")):
            return True
    return False


class _ImportGuardVisitor(ast.NodeVisitor):
    """Collects Import/ImportFrom nodes that are NOT protected by a
    try/except and NOT nested inside a function body — i.e. imports that
    actually execute, unconditionally, the moment the module is imported.
    A guarded or deferred import failing does not make "the module raise on
    import"; that is exactly what distinguishes an optional dependency
    (nasdaqdatalink in a try/except, hmmlearn inside a method) from a real
    D1-style landmine (kelly_engine.py's top-level `from layer2...`)."""

    def __init__(self):
        self.unguarded: list[ast.AST] = []
        self._guard_depth = 0

    def visit_FunctionDef(self, node):
        self._guard_depth += 1
        self.generic_visit(node)
        self._guard_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef

    def visit_Try(self, node):
        # only the `try` body is guarded from the caller's perspective
        self._guard_depth += 1
        for child in node.body:
            self.visit(child)
        self._guard_depth -= 1
        for child in node.handlers + node.orelse + node.finalbody:
            self.visit(child)

    def visit_Import(self, node):
        if self._guard_depth == 0:
            self.unguarded.append(node)

    def visit_ImportFrom(self, node):
        if self._guard_depth == 0:
            self.unguarded.append(node)


def find_broken_imports(modules: dict[str, ParsedModule], all_module_names: set[str]) -> list[dict]:
    findings = []
    for pm in modules.values():
        if pm.tree is None:
            findings.append({
                "module": pm.module_name,
                "path": rel_str(pm.path),
                "evidence": f"SyntaxError: {pm.parse_error}",
            })
            continue
        is_package = pm.path.name == "__init__.py"
        current_package = ".".join(pm.module_name.split(".")[:-1]) if not is_package else pm.module_name
        extra_prefixes = tuple(_extra_syspath_prefixes(pm.source))
        visitor = _ImportGuardVisitor()
        visitor.visit(pm.tree)
        for node in visitor.unguarded:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not _top_level_resolvable(alias.name, all_module_names, current_package, extra_prefixes):
                        findings.append({
                            "module": pm.module_name,
                            "path": rel_str(pm.path),
                            "evidence": f"line {node.lineno}: `import {alias.name}` — "
                                        f"'{alias.name.split('.')[0]}' not found in repo, "
                                        f"stdlib, requirements*.txt, or site-packages",
                        })
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    base = resolve_relative(pm.module_name, is_package, node.level, node.module)
                else:
                    base = node.module or ""
                if base and not _top_level_resolvable(base, all_module_names, current_package, extra_prefixes):
                    findings.append({
                        "module": pm.module_name,
                        "path": rel_str(pm.path),
                        "evidence": f"line {node.lineno}: `from {'.' * node.level}{node.module or ''} "
                                    f"import ...` — '{base.split('.')[0]}' not found in repo, "
                                    f"stdlib, requirements*.txt, or site-packages",
                    })
    # de-dup per module (report first offending line, note if more)
    by_module: dict[str, list[dict]] = {}
    for f in findings:
        by_module.setdefault(f["module"], []).append(f)
    deduped = []
    for mod, items in by_module.items():
        first = items[0]
        if len(items) > 1:
            first = dict(first)
            first["evidence"] += f" (+{len(items) - 1} more unresolvable import(s) in this module)"
        deduped.append(first)
    return deduped


# --------------------------------------------------------------------------
# Category 1: ORPHANS
# --------------------------------------------------------------------------

def _find_dynamic_import_prefixes(modules: dict[str, ParsedModule]) -> set[str]:
    """Best-effort: find `import_module(f"pkg.sub.{name}")`-style calls and
    return the static dotted prefix (e.g. "sovereign.risk.layers") so every
    module directly under that package is treated as reachable via a dynamic
    dispatch table, not a static import."""
    prefixes: set[str] = set()
    pattern = re.compile(r'import_module\(\s*f?["\']([a-zA-Z0-9_.]+)\.\{')
    for pm in modules.values():
        for m in pattern.finditer(pm.source):
            prefixes.add(m.group(1))
        # also handle f"{pkg}.{name}" .format(...) style with a preceding literal assignment is
        # out of scope; the f-string prefix form above covers the known case in risk_engine.py.
    return prefixes


def _shell_script_targets() -> set[str]:
    """Modules invoked as `python3 X.py` (or `python3 path/to/X.py`) from a
    LaunchAgent-driven shell script — a legitimate caller with no Python
    import edge."""
    targets: set[str] = set()
    pattern = re.compile(r'python3\s+(?:"\$[A-Z_]+["/]*[\w./]*"/)?["]?([\w./]+\.py)')
    for script in SHELL_CALLERS:
        if not script.exists():
            continue
        text = script.read_text()
        for m in re.finditer(r'python3\s+"?(?:\$\{?[A-Z_]+\}?/)?([\w./]*\.py)"?', text):
            fname = Path(m.group(1)).name
            targets.add(fname[:-3])  # module basename, without .py
    return targets


def find_orphans(modules: dict[str, ParsedModule], all_module_names: set[str]) -> list[dict]:
    dynamic_prefixes = _find_dynamic_import_prefixes(modules)
    shell_targets = _shell_script_targets()

    importers: dict[str, set[str]] = {m: set() for m in all_module_names}
    for pm in modules.values():
        edges = module_imports(pm, all_module_names)
        for e in edges:
            tgt = e.target_module
            if tgt in importers:
                importers[tgt].add(pm.module_name)
            else:
                # a `from pkg import symbol` where pkg itself IS a target module
                # (not the symbol) still counts as importing pkg
                if e.symbol is not None and e.target_module in importers:
                    importers[e.target_module].add(pm.module_name)

    findings = []
    for mod_name, pm in modules.items():
        if pm.is_test:
            continue
        if pm.path.name == "__init__.py":
            continue
        non_test_importers = {i for i in importers.get(mod_name, set()) if not modules[i].is_test}
        if non_test_importers:
            continue

        # exemption: dynamically dispatched (e.g. sovereign/risk/layers/*)
        parent_pkg = ".".join(mod_name.split(".")[:-1])
        if parent_pkg in dynamic_prefixes:
            continue

        # exemption: invoked as a CLI entry point from a shell script
        base = mod_name.split(".")[-1]
        if base in shell_targets:
            continue

        # exemption: has its own __main__ entry AND lives directly under
        # scripts/ (the whole directory's contract is "run me by hand") —
        # still reported, but with weaker evidence text; scripts/ is
        # expected to have many legitimate zero-Python-importer CLI tools.
        has_main = pm.tree is not None and any(
            isinstance(n, ast.If)
            and isinstance(n.test, ast.Compare)
            and isinstance(n.test.left, ast.Name)
            and n.test.left.id == "__name__"
            for n in ast.walk(pm.tree)
        )

        evidence = "no importer outside test files"
        if has_main:
            evidence += " (has `if __name__ == \"__main__\":` — may be a standalone CLI tool)"

        findings.append({
            "module": mod_name,
            "path": rel_str(pm.path),
            "evidence": evidence,
        })
    return findings


# --------------------------------------------------------------------------
# Category 2: IMPORTED_NOT_CALLED
# --------------------------------------------------------------------------

def _names_used_as_call_func(tree: ast.Module) -> set[str]:
    """Local binding names that appear as the direct func of some Call, OR as
    the base of an attribute access that is itself called (Y.foo(...))."""
    used: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            used.add(func.id)
        elif isinstance(func, ast.Attribute):
            base = func.value
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                used.add(base.id)
    return used


def find_imported_not_called(modules: dict[str, ParsedModule], all_module_names: set[str]) -> list[dict]:
    findings = []
    for pm in modules.values():
        if pm.tree is None or pm.is_test:
            continue
        edges = module_imports(pm, all_module_names)
        if not edges:
            continue
        called_names = _names_used_as_call_func(pm.tree)

        # group edges by which project module they ultimately come from
        per_module_locals: dict[str, list[ImportEdge]] = {}
        for e in edges:
            if e.star:
                continue
            if e.target_module not in all_module_names:
                continue  # only care about project-internal wiring, not stdlib/3rd-party
            if e.target_module == pm.module_name:
                continue  # self
            per_module_locals.setdefault(e.target_module, []).append(e)

        for target, local_edges in per_module_locals.items():
            if any(e.local_name in called_names for e in local_edges):
                continue
            local_names = ", ".join(sorted({e.local_name for e in local_edges}))
            findings.append({
                "module": pm.module_name,
                "path": rel_str(pm.path),
                "evidence": f"imports `{target}` (as {local_names}) but never calls "
                            f"any symbol from it",
                "target": target,
            })
    return findings


# --------------------------------------------------------------------------
# Category 3: NO_SUPPLIER (consumer with no supplier)
# --------------------------------------------------------------------------

def _dataclass_none_default_fields(tree: ast.Module, module_name: str) -> list[tuple[str, str]]:
    """Returns [(class_name, field_name), ...] for @dataclass fields whose
    default is literal None (directly, or via `field(default=None)`)."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        is_dataclass = any(
            (isinstance(d, ast.Name) and d.id == "dataclass")
            or (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "dataclass")
            for d in node.decorator_list
        )
        if not is_dataclass:
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                continue
            default = stmt.value
            is_none_default = False
            if isinstance(default, ast.Constant) and default.value is None:
                is_none_default = True
            elif isinstance(default, ast.Call) and isinstance(default.func, ast.Name) and default.func.id == "field":
                for kw in default.keywords:
                    if kw.arg == "default" and isinstance(kw.value, ast.Constant) and kw.value.value is None:
                        is_none_default = True
            if is_none_default:
                out.append((node.name, stmt.target.id))
    return out


def _is_none_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def find_no_supplier(modules: dict[str, ParsedModule]) -> list[dict]:
    candidates: dict[str, list[tuple[str, str]]] = {}  # module -> [(class, field)]
    for pm in modules.values():
        if pm.tree is None:
            continue
        fields = _dataclass_none_default_fields(pm.tree, pm.module_name)
        if fields:
            candidates[pm.module_name] = fields

    all_field_names = {f for fields in candidates.values() for (_, f) in fields}
    if not all_field_names:
        return []

    reads: dict[str, list[str]] = {f: [] for f in all_field_names}   # field -> [locations]
    supplies: dict[str, list[str]] = {f: [] for f in all_field_names}

    for pm in modules.values():
        if pm.tree is None:
            continue
        for node in ast.walk(pm.tree):
            # reads: attribute Load access `.field`
            if isinstance(node, ast.Attribute) and node.attr in all_field_names and isinstance(node.ctx, ast.Load):
                if not pm.is_test:
                    reads[node.attr].append(f"{rel_str(pm.path)}:{node.lineno}")
            # supplies (production only): keyword arg with non-None value
            if not pm.is_test:
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg in all_field_names and not _is_none_literal(kw.value):
                            supplies[kw.arg].append(f"{rel_str(pm.path)}:{node.lineno} (constructor kwarg)")
                # supplies: attribute Store assignment `.field = non-None`
                if isinstance(node, ast.Attribute) and node.attr in all_field_names and isinstance(node.ctx, ast.Store):
                    supplies[node.attr].append(f"{rel_str(pm.path)}:{node.lineno} (attribute assignment)")

    findings = []
    for mod_name, fields in candidates.items():
        for class_name, field_name in fields:
            if not reads.get(field_name):
                continue  # never read at all — not a wiring problem, just an unused field
            if supplies.get(field_name):
                continue  # has at least one non-test, non-None supplier
            findings.append({
                "module": mod_name,
                "path": rel_str(modules[mod_name].path),
                "evidence": f"{class_name}.{field_name} defaults to None; read at "
                            f"{reads[field_name][0]}"
                            + (f" (+{len(reads[field_name]) - 1} more)" if len(reads[field_name]) > 1 else "")
                            + "; never supplied a non-None value in production code",
                "field": f"{class_name}.{field_name}",
            })
    return findings


# --------------------------------------------------------------------------
# Category 5: DEAD_ARTIFACT
# --------------------------------------------------------------------------

WRITE_CALL_NAMES = {
    "to_parquet", "to_csv", "to_json", "write_text", "write_bytes", "dump",
}
READ_CALL_NAMES = {
    "read_parquet", "read_csv", "read_json", "read_text", "read_bytes", "load",
    "loads",
}

_DATA_PATH_RE = re.compile(r'^(data/[\w./\-]+\.\w+)$')


def _open_mode_is_write(node: ast.Call) -> Optional[bool]:
    """For an `open(...)` call, True if write mode, False if read mode, None if unknown."""
    mode = None
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        mode = node.args[1].value
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    if mode is None:
        return False  # default 'r'
    if isinstance(mode, str) and any(c in mode for c in "wax"):
        return True
    return False


def _flatten_path_join(node: ast.AST, resolved_vars: dict[str, str]) -> Optional[str]:
    """Resolve `ROOT / "data" / "daytrade" / "directives.json"`-style BinOp
    chains (the dominant path-construction idiom in this repo) to a
    "data/..." suffix string. The leftmost operand is always an anchor
    (ROOT/REPO_ROOT/HERE/...) and is dropped; if it's itself a Name already
    resolved to a data/... suffix earlier in the same module (e.g.
    `OUT = ROOT / "data" / "daytrade"`, then `DIRECTIVES = OUT / "x.json"`)
    that prior resolution is used as the prefix."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
        right = cur.right
        if isinstance(right, ast.Constant) and isinstance(right.value, str):
            parts.append(right.value)
        else:
            return None
        cur = cur.left
    # `cur` is now the leftmost operand
    prefix = ""
    if isinstance(cur, ast.Name) and cur.id in resolved_vars:
        prefix = resolved_vars[cur.id]
    elif isinstance(cur, ast.Constant) and isinstance(cur.value, str):
        prefix = cur.value
    parts.reverse()
    joined = "/".join([p for p in ([prefix] if prefix else []) + parts])
    return joined or None


def _module_path_constants(pm: ParsedModule) -> dict[str, str]:
    """Module-level (or nested) `NAME = <path-join expr>` assignments,
    resolved to a "data/..." suffix where possible. Two-pass within the
    module body so `OUT = ROOT / "data" / "daytrade"` can feed
    `DIRECTIVES = OUT / "directives.json"`."""
    if pm.tree is None:
        return {}
    resolved: dict[str, str] = {}
    # iterate assignments in source order (ast.walk is not guaranteed source
    # order across nesting, so do a manual top-down walk that still visits in
    # document order for the common case of straight-line module code)
    for node in ast.walk(pm.tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            suffix = _flatten_path_join(node.value, resolved)
            if suffix and (suffix.startswith("data/") or "/data/" in suffix):
                if not suffix.startswith("data/"):
                    suffix = suffix[suffix.index("data/"):]
                resolved[node.targets[0].id] = suffix
    return resolved


def find_dead_artifacts(modules: dict[str, ParsedModule], orphan_modules: set[str]) -> list[dict]:
    # path -> [(location, "read"/"write")]
    events: dict[str, list[tuple[str, str]]] = {}
    # path -> {module_name defining/using it, ...} for orphan cross-reference
    path_modules: dict[str, set[str]] = {}

    for pm in modules.values():
        if pm.tree is None:
            continue
        path_consts = _module_path_constants(pm)  # varname -> data/... suffix

        # classify each Call in this module as read-like / write-like, and
        # figure out which path (literal OR resolved constant) it targets.
        for node in ast.walk(pm.tree):
            if not isinstance(node, ast.Call):
                continue
            fname = None
            if isinstance(node.func, ast.Name):
                fname = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fname = node.func.attr
            if fname is None:
                continue

            kind = None
            if fname == "open":
                kind = "write" if _open_mode_is_write(node) else "read"
            elif fname in WRITE_CALL_NAMES:
                kind = "write"
            elif fname in READ_CALL_NAMES:
                kind = "read"
            if kind is None:
                continue

            # figure out the target path: first arg to open()/read_*/to_*,
            # or (for .write_text()/.read_text()/.to_parquet() called AS A
            # METHOD on the path object) the object the method is called on.
            target_expr = None
            if node.args:
                target_expr = node.args[0]
            if isinstance(node.func, ast.Attribute) and fname in (
                "write_text", "write_bytes", "read_text", "read_bytes",
                "to_parquet", "to_csv", "to_json",
            ):
                target_expr = node.func.value

            path_str = None
            if isinstance(target_expr, ast.Constant) and isinstance(target_expr.value, str):
                m = _DATA_PATH_RE.search(target_expr.value)
                if m:
                    path_str = m.group(1)
            elif isinstance(target_expr, ast.Name) and target_expr.id in path_consts:
                path_str = path_consts[target_expr.id]
            elif isinstance(target_expr, ast.BinOp):
                resolved = _flatten_path_join(target_expr, path_consts)
                if resolved and "data/" in resolved:
                    path_str = resolved[resolved.index("data/"):]

            if path_str is None:
                continue
            loc = f"{rel_str(pm.path)}:{node.lineno}"
            events.setdefault(path_str, []).append((loc, kind))
            path_modules.setdefault(path_str, set()).add(pm.module_name)

    findings = []
    for path_str, occs in events.items():
        writes = [loc for loc, k in occs if k == "write"]
        reads = [(loc, k) for loc, k in occs if k == "read"]
        if not writes:
            continue
        # which modules performed a read?
        read_modules = {
            loc.split(":")[0] for loc, k in occs if k == "read"
        }
        read_module_names = {
            m for m, pm in modules.items() if rel_str(pm.path) in read_modules
        }
        non_orphan_readers = read_module_names - orphan_modules
        if non_orphan_readers:
            continue  # genuinely consumed by live code — not dead

        evidence = f"written at {writes[0]}" + (f" (+{len(writes) - 1} more)" if len(writes) > 1 else "")
        if read_module_names:
            evidence += ("; only reader(s) are orphaned module(s) with no importer "
                         f"outside test files: {', '.join(sorted(read_module_names))}")
        else:
            evidence += "; no read of this path found anywhere in production code"

        findings.append({
            "module": path_str,
            "path": path_str,
            "evidence": evidence,
        })
    return findings


# --------------------------------------------------------------------------
# Allowlist
# --------------------------------------------------------------------------

def _parse_simple_yaml(text: str) -> dict:
    """Minimal YAML-subset parser (no PyYAML dependency assumed). Handles the
    shape this file uses:

    category:
      - key: value
        reason: "..."
    """
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    except ImportError:
        pass
    # fallback hand-rolled parser for the specific structure we write below
    data: dict = {}
    current_cat = None
    current_item: dict | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0 and stripped.endswith(":"):
            current_cat = stripped[:-1]
            data[current_cat] = []
            current_item = None
            continue
        if stripped.startswith("- "):
            current_item = {}
            data[current_cat].append(current_item)
            stripped = stripped[2:]
        if ":" in stripped and current_item is not None:
            k, _, v = stripped.partition(":")
            v = v.strip()
            if v.startswith('"') and v.endswith('"'):
                v = v[1:-1]
            elif v.startswith("'") and v.endswith("'"):
                v = v[1:-1]
            current_item[k.strip()] = v
    return data


def load_allowlist() -> dict[str, list[dict]]:
    if not ALLOWLIST_PATH.exists():
        return {}
    text = ALLOWLIST_PATH.read_text()
    data = _parse_simple_yaml(text)
    return data or {}


def validate_allowlist(allowlist: dict) -> list[str]:
    """Every entry must have a non-empty reason. Returns a list of validation errors."""
    errors = []
    for category, entries in allowlist.items():
        if not entries:
            continue
        for entry in entries:
            reason = (entry or {}).get("reason", "").strip()
            if not reason:
                key = entry.get("module") or entry.get("field") or entry.get("target") or entry
                errors.append(f"{category}: entry {key!r} has no reason string")
    return errors


def _allowlist_key(category: str, finding: dict) -> str:
    if category == "NO_SUPPLIER":
        return finding.get("field", finding["module"])
    if category == "IMPORTED_NOT_CALLED":
        return f"{finding['module']}->{finding.get('target', '')}"
    return finding["module"]


def apply_allowlist(category: str, findings: list[dict], allowlist: dict) -> tuple[list[dict], list[dict]]:
    """Returns (still_flagged, allowlisted_with_reason)."""
    entries = allowlist.get(category, []) or []
    allowed_keys = {}
    for e in entries:
        key = e.get("module") or e.get("field") or e.get("target")
        if key:
            allowed_keys[key] = e.get("reason", "")

    flagged = []
    allowlisted = []
    for f in findings:
        key = _allowlist_key(category, f)
        # also allow a bare module-name match for IMPORTED_NOT_CALLED / NO_SUPPLIER
        alt_key = f.get("module")
        if key in allowed_keys:
            f = dict(f)
            f["reason"] = allowed_keys[key]
            allowlisted.append(f)
        elif alt_key in allowed_keys:
            f = dict(f)
            f["reason"] = allowed_keys[alt_key]
            allowlisted.append(f)
        else:
            flagged.append(f)
    return flagged, allowlisted


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

CATEGORIES = ["ORPHANS", "IMPORTED_NOT_CALLED", "NO_SUPPLIER", "BROKEN_IMPORT", "DEAD_ARTIFACT"]


def run_audit() -> dict:
    modules = parse_all()
    all_module_names = set(modules.keys())

    orphan_findings = find_orphans(modules, all_module_names)
    orphan_modules = {f["module"] for f in orphan_findings}

    raw = {
        "ORPHANS": orphan_findings,
        "IMPORTED_NOT_CALLED": find_imported_not_called(modules, all_module_names),
        "NO_SUPPLIER": find_no_supplier(modules),
        "BROKEN_IMPORT": find_broken_imports(modules, all_module_names),
        "DEAD_ARTIFACT": find_dead_artifacts(modules, orphan_modules),
    }

    allowlist = load_allowlist()
    allowlist_errors = validate_allowlist(allowlist)

    results = {}
    for cat in CATEGORIES:
        flagged, allowlisted = apply_allowlist(cat, raw[cat], allowlist)
        results[cat] = {"flagged": flagged, "allowlisted": allowlisted}

    return {
        "results": results,
        "allowlist_errors": allowlist_errors,
        "module_count": len(all_module_names),
    }


def render_report(audit: dict) -> str:
    lines = []
    lines.append("=" * 78)
    lines.append("WIRING AUDIT — built/tested/never-wired detector")
    lines.append("=" * 78)
    lines.append(f"scanned {audit['module_count']} modules under "
                  f"{', '.join(TARGET_DIRS)}")
    lines.append("")

    if audit["allowlist_errors"]:
        lines.append("ALLOWLIST ERRORS (entries missing a reason string):")
        for e in audit["allowlist_errors"]:
            lines.append(f"  ! {e}")
        lines.append("")

    total_flagged = 0
    total_allowlisted = 0
    for cat in CATEGORIES:
        flagged = audit["results"][cat]["flagged"]
        allowlisted = audit["results"][cat]["allowlisted"]
        total_flagged += len(flagged)
        total_allowlisted += len(allowlisted)
        lines.append(f"[{cat}] {len(flagged)} flagged, {len(allowlisted)} allowlisted")
        for f in flagged:
            lines.append(f"  NEW  {f['module']}")
            lines.append(f"       {f['path']}: {f['evidence']}")
        for f in allowlisted:
            lines.append(f"  OK   {f['module']}  (allowlisted: {f['reason']})")
        lines.append("")

    lines.append("-" * 78)
    lines.append(f"TOTAL: {total_flagged} new/unexplained, {total_allowlisted} allowlisted "
                  f"across {len(CATEGORIES)} categories")
    return "\n".join(lines)


def main() -> int:
    audit = run_audit()
    if "--json" in sys.argv:
        print(json.dumps(audit, indent=2, default=str))
    else:
        print(render_report(audit))

    exit_code = 0
    if audit["allowlist_errors"]:
        exit_code = 1
    for cat in CATEGORIES:
        if audit["results"][cat]["flagged"]:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

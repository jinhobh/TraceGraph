"""Import resolution: turn a parsed module into dependency edges.

This module is pure and side-effect-free: input is a parsed AST plus the
module's identity and the project's module index; output is a list of edges.
All I/O and discovery live in ``discovery``.

Every edge carries a ``context`` tag — ``module`` for imports executed at load
time, ``function`` for imports deferred into a function body, and
``type_checking`` for imports guarded by ``typing.TYPE_CHECKING``. The tag is
load-bearing: cycle detection uses only ``module`` edges, while test impact
analysis uses all of them.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from typing import Literal

Context = Literal["module", "function", "type_checking"]
Category = Literal["first_party", "stdlib", "third_party", "unknown"]

#: Edge target used for dynamic imports whose name cannot be determined
#: statically. These are surfaced in reports as known blind spots.
DYNAMIC_TARGET = "<dynamic>"

#: Modules that are import machinery rather than real dependencies.
_IGNORED_MODULES = frozenset({"__future__"})


@dataclass(frozen=True, slots=True)
class Edge:
    """A dependency of module ``src`` on ``dst`` found in ``src``'s source.

    Edges connect modules, not symbols: ``from pkg import name`` where
    ``name`` is not a submodule produces an edge to ``pkg``.
    """

    src: str
    dst: str
    context: Context
    resolved: bool
    category: Category


def resolve_module(
    tree: ast.Module,
    name: str,
    is_package: bool,
    index: frozenset[str],
) -> list[Edge]:
    """Resolve every import in ``tree`` into edges out of module ``name``.

    ``index`` is the full set of first-party module names; a target is
    first-party iff its top-level segment is a first-party package root.
    Duplicate edges are removed; order of first occurrence is preserved.
    """
    visitor = _ImportVisitor(name, is_package, index)
    visitor.visit(tree)
    return visitor.edges


def _is_type_checking(test: ast.expr) -> bool:
    """True for ``TYPE_CHECKING`` or ``typing.TYPE_CHECKING`` guards."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return (
            test.attr == "TYPE_CHECKING"
            and isinstance(test.value, ast.Name)
            and test.value.id == "typing"
        )
    return False


def _is_dynamic_import(func: ast.expr) -> bool:
    """True for calls to ``importlib.import_module``/``import_module``/
    ``__import__``."""
    if isinstance(func, ast.Name):
        return func.id in ("import_module", "__import__")
    if isinstance(func, ast.Attribute):
        return (
            func.attr == "import_module"
            and isinstance(func.value, ast.Name)
            and func.value.id == "importlib"
        )
    return False


class _ImportVisitor(ast.NodeVisitor):
    """Walk a module body collecting edges, tracking the execution context."""

    def __init__(self, module: str, is_package: bool, index: frozenset[str]) -> None:
        self._module = module
        self._is_package = is_package
        self._index = index
        self._roots = {name.split(".", 1)[0] for name in index}
        self._context: list[Context] = ["module"]
        self._seen: set[Edge] = set()
        self.edges: list[Edge] = []

    # -- edge emission -----------------------------------------------------

    def _add(self, dst: str, resolved: bool, category: Category) -> None:
        if dst == self._module or dst in _IGNORED_MODULES:
            return
        edge = Edge(self._module, dst, self._context[-1], resolved, category)
        if edge not in self._seen:
            self._seen.add(edge)
            self.edges.append(edge)

    def _add_external(self, name: str) -> None:
        # External modules are leaf nodes: record the top-level name only and
        # never traverse into them.
        top = name.split(".", 1)[0]
        category: Category = (
            "stdlib" if top in sys.stdlib_module_names else "third_party"
        )
        self._add(top, True, category)

    def _add_absolute(self, name: str) -> None:
        """Emit the edge for an absolute dotted import of ``name``."""
        if name.split(".", 1)[0] not in self._roots:
            self._add_external(name)
        elif name in self._index:
            self._add(name, True, "first_party")
        else:
            # First-party prefix but no such module: keep the full target and
            # flag it unresolved so the report can surface the broken import.
            self._add(name, False, "first_party")

    def _add_first_party_module(self, name: str) -> None:
        self._add(name, name in self._index, "first_party")

    # -- context tracking ----------------------------------------------------

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Decorators, defaults, and annotations run at definition time, in the
        # enclosing context; only the body is deferred.
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        inner: Context = (
            "type_checking" if self._context[-1] == "type_checking" else "function"
        )
        self._context.append(inner)
        for stmt in node.body:
            self.visit(stmt)
        self._context.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        if _is_type_checking(node.test):
            # A TYPE_CHECKING guard never executes at runtime, even inside a
            # function body.
            self._context.append("type_checking")
            for stmt in node.body:
                self.visit(stmt)
            self._context.pop()
        else:
            for stmt in node.body:
                self.visit(stmt)
        for stmt in node.orelse:
            self.visit(stmt)

    # -- import statements -----------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._add_absolute(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = self._resolve_base(node)
        if base is None:
            # Relative import that escapes the top-level package: broken at
            # runtime, surfaced as unresolved rather than dropped.
            target = "." * node.level + (node.module or "")
            self._add(target, False, "unknown")
            return
        if base.split(".", 1)[0] not in self._roots:
            self._add_external(base)
            return
        for alias in node.names:
            if alias.name == "*":
                self._add_first_party_module(base)
                continue
            # Probe for ``base.name`` as a module FIRST; only fall back to
            # "name is defined in base" if the index has no such module.
            submodule = f"{base}.{alias.name}"
            if submodule in self._index:
                self._add(submodule, True, "first_party")
            else:
                self._add_first_party_module(base)

    def _resolve_base(self, node: ast.ImportFrom) -> str | None:
        """Anchor a (possibly relative) ``from`` import to an absolute name.

        Leading dots count up from the current module's package: a package
        ``__init__`` anchors to the package itself, a regular module to its
        containing package. Returns None when the dots escape the top-level
        package.
        """
        if node.level == 0:
            return node.module
        parts = self._module.split(".")
        if not self._is_package:
            parts = parts[:-1]
        if node.level > len(parts):
            return None
        parts = parts[: len(parts) - (node.level - 1)]
        if node.module:
            parts.extend(node.module.split("."))
        return ".".join(parts)

    # -- dynamic imports -----------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        if _is_dynamic_import(node.func):
            arg = node.args[0] if node.args else None
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and not arg.value.startswith(".")
            ):
                self._add_absolute(arg.value)
            else:
                self._add(DYNAMIC_TARGET, False, "unknown")
        self.generic_visit(node)

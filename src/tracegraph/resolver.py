"""Import resolution: turn a parsed module into dependency edges.

This module is pure and side-effect-free: input is a parsed AST plus the
module's identity and the project's module index; output is a list of edges.
All I/O and discovery live in ``discovery``.

Every edge carries a ``context`` tag — ``module`` for imports executed at load
time, ``function`` for imports deferred into a function body, and
``type_checking`` for imports guarded by ``typing.TYPE_CHECKING``. The tag is
load-bearing: cycle detection uses only ``module`` edges, while test impact
analysis uses all of them.

Every edge also carries a ``binding`` tag — ``module`` when the import only
binds a module object (plain ``import x``, ``from pkg import submodule``,
dynamic imports), ``symbol`` when load-time code needs names out of the
target's namespace (``from x import name`` where ``name`` is not a submodule,
``from x import *``, or a module-object import whose bound name is attribute-
accessed at module scope). Python tolerates circular imports realized purely
through ``module`` bindings — a partially initialized module object satisfies
them — so only ``symbol`` bindings can turn a cycle into a load-time failure.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, replace
from typing import Literal

Context = Literal["module", "function", "type_checking"]
Category = Literal["first_party", "stdlib", "third_party", "unknown"]
Binding = Literal["module", "symbol"]

#: Edge target used for dynamic imports whose name cannot be determined
#: statically. These are surfaced in reports as known blind spots.
DYNAMIC_TARGET = "<dynamic>"

#: Modules that are import machinery rather than real dependencies.
_IGNORED_MODULES = frozenset({"__future__"})


@dataclass(frozen=True, slots=True)
class Edge:
    """A dependency of module ``src`` on ``dst`` found in ``src``'s source.

    Edges connect modules, not symbols: ``from pkg import name`` where
    ``name`` is not a submodule produces an edge to ``pkg``. ``binding``
    records whether the edge is satisfied by a bare module object
    (``module``) or needs names from a fully initialized target
    (``symbol``); see the module docstring.
    """

    src: str
    dst: str
    context: Context
    resolved: bool
    category: Category
    binding: Binding


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
    return visitor.finalize()


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
        # Local names bound to module objects by module-context imports, and
        # names attribute-accessed at module scope. Together these decide
        # which module-binding edges get upgraded to symbol in finalize().
        self._module_bound: dict[str, set[str]] = {}
        self._attr_used: set[str] = set()
        # Local names the guard check accepts, kept in sync with import
        # aliases so ``import typing as t; if t.TYPE_CHECKING:`` is guarded.
        self._typing_aliases = {"typing"}
        self._tc_aliases = {"TYPE_CHECKING"}

    def _is_type_checking(self, test: ast.expr) -> bool:
        """True for ``TYPE_CHECKING`` guards, under whatever local alias
        ``typing`` or ``TYPE_CHECKING`` was imported as."""
        if isinstance(test, ast.Name):
            return test.id in self._tc_aliases
        if isinstance(test, ast.Attribute):
            return (
                test.attr == "TYPE_CHECKING"
                and isinstance(test.value, ast.Name)
                and test.value.id in self._typing_aliases
            )
        return False

    def finalize(self) -> list[Edge]:
        """Return the edges, upgrading module bindings used at module scope.

        A plain ``import x`` binds only the module object, but if module-scope
        code then reads ``x.attr``, load time still needs ``x``'s namespace —
        so the edge is as cycle-fatal as ``from x import attr``. The upgrade
        is per bound name, not per attribute: any module-scope attribute
        access through a name upgrades every edge that name's imports emitted.
        """
        upgraded = {
            dst
            for name, dsts in self._module_bound.items()
            if name in self._attr_used
            for dst in dsts
        }
        edges: list[Edge] = []
        seen: set[Edge] = set()
        for edge in self.edges:
            if (
                edge.context == "module"
                and edge.binding == "module"
                and edge.dst in upgraded
            ):
                edge = replace(edge, binding="symbol")
            if edge not in seen:
                seen.add(edge)
                edges.append(edge)
        return edges

    # -- edge emission -----------------------------------------------------

    def _add(
        self, dst: str, resolved: bool, category: Category, binding: Binding
    ) -> str | None:
        """Emit an edge to ``dst``; returns ``dst`` if an edge applies."""
        if dst == self._module or dst in _IGNORED_MODULES:
            return None
        edge = Edge(self._module, dst, self._context[-1], resolved, category, binding)
        if edge not in self._seen:
            self._seen.add(edge)
            self.edges.append(edge)
        return dst

    def _add_external(self, name: str, binding: Binding) -> str | None:
        # External modules are leaf nodes: record the top-level name only and
        # never traverse into them.
        top = name.split(".", 1)[0]
        category: Category = (
            "stdlib" if top in sys.stdlib_module_names else "third_party"
        )
        return self._add(top, True, category, binding)

    def _add_absolute(self, name: str) -> str | None:
        """Emit the module-binding edge for an absolute dotted import."""
        if name.split(".", 1)[0] not in self._roots:
            return self._add_external(name, "module")
        if name in self._index:
            return self._add(name, True, "first_party", "module")
        # First-party prefix but no such module: keep the full target and
        # flag it unresolved so the report can surface the broken import.
        return self._add(name, False, "first_party", "module")

    def _add_first_party_module(self, name: str, binding: Binding) -> str | None:
        return self._add(name, name in self._index, "first_party", binding)

    def _record_bound(self, local_name: str, dst: str | None) -> None:
        """Remember that module-scope ``local_name`` holds module ``dst``."""
        if dst is not None and self._context[-1] == "module":
            self._module_bound.setdefault(local_name, set()).add(dst)

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
        if self._is_type_checking(node.test):
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
            if alias.name == "typing":
                self._typing_aliases.add(alias.asname or "typing")
            dst = self._add_absolute(alias.name)
            # ``import a.b`` binds ``a``; ``import a.b as m`` binds ``m``.
            self._record_bound(alias.asname or alias.name.split(".", 1)[0], dst)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module == "typing":
            for alias in node.names:
                if alias.name == "TYPE_CHECKING":
                    self._tc_aliases.add(alias.asname or "TYPE_CHECKING")
        base = self._resolve_base(node)
        if base is None:
            # Relative import that escapes the top-level package: broken at
            # runtime, surfaced as unresolved rather than dropped.
            target = "." * node.level + (node.module or "")
            self._add(target, False, "unknown", "symbol")
            return
        if base.split(".", 1)[0] not in self._roots:
            self._add_external(base, "symbol")
            return
        for alias in node.names:
            if alias.name == "*":
                # A star import eagerly reads the target's namespace.
                self._add_first_party_module(base, "symbol")
                continue
            # Probe for ``base.name`` as a module FIRST; only fall back to
            # "name is defined in base" if the index has no such module.
            submodule = f"{base}.{alias.name}"
            if submodule in self._index:
                dst = self._add(submodule, True, "first_party", "module")
                self._record_bound(alias.asname or alias.name, dst)
            else:
                self._add_first_party_module(base, "symbol")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self._context[-1] == "module" and isinstance(node.value, ast.Name):
            self._attr_used.add(node.value.id)
        self.generic_visit(node)

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
                # import_module returns a module object; symbol use happens
                # later, past what static analysis can see.
                self._add(DYNAMIC_TARGET, False, "unknown", "module")
        self.generic_visit(node)

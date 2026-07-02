"""Project discovery: walk the tree, build the module index, parse sources.

All filesystem I/O lives here; ``resolver`` is pure and operates only on the
data structures this module produces.

A module is first-party iff it lives under one of the project's source roots:
``<root>/src`` when present (src/ layout) plus the root itself. Directories
without ``__init__.py`` that contain modules are treated as PEP 420 namespace
packages. Files that fail to parse are recorded and skipped, never fatal.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

_EXCLUDED_DIRS = frozenset(
    {"__pycache__", "node_modules", "build", "dist", "venv", ".venv"}
)


@dataclass
class Module:
    """A first-party module discovered in the project.

    ``tree`` is None for namespace packages (no source of their own) and for
    files that failed to parse.
    """

    name: str
    path: Path
    is_package: bool
    tree: ast.Module | None = None


@dataclass
class ParseError:
    """A file that could not be parsed, recorded instead of raised."""

    path: Path
    message: str


@dataclass
class Project:
    """The module index and parse diagnostics for one project root."""

    root: Path
    modules: dict[str, Module] = field(default_factory=dict)
    parse_errors: list[ParseError] = field(default_factory=list)

    @property
    def index(self) -> frozenset[str]:
        return frozenset(self.modules)


def discover(root: Path | str) -> Project:
    """Build the module index for the project rooted at ``root``."""
    root_path = Path(root).resolve()
    project = Project(root=root_path)
    src = root_path / "src"
    source_roots = [src, root_path] if src.is_dir() else [root_path]
    for source_root in source_roots:
        skip = {other for other in source_roots if other != source_root}
        _walk_root(source_root, project, skip)
    return project


def module_for_path(project: Project, path: Path | str) -> str | None:
    """Map a file path to its module name, or None if it is not a module."""
    resolved = Path(path).resolve()
    for module in project.modules.values():
        if module.path == resolved:
            return module.name
    return None


def _walk_root(source_root: Path, project: Project, skip: set[Path]) -> None:
    for entry in sorted(source_root.iterdir()):
        if _skip_entry(entry) or entry in skip:
            continue
        if entry.is_dir():
            _walk_package(entry, entry.name, project)
        elif _is_module_file(entry):
            _register_module(project, entry, entry.stem, is_package=False)


def _walk_package(directory: Path, dotted: str, project: Project) -> bool:
    """Register ``directory`` and everything under it.

    Returns True if the directory holds any modules and is therefore
    importable (a regular package or a PEP 420 namespace package).
    """
    found = False
    for entry in sorted(directory.iterdir()):
        if _skip_entry(entry):
            continue
        if entry.is_dir():
            found |= _walk_package(entry, f"{dotted}.{entry.name}", project)
        elif _is_module_file(entry):
            _register_module(project, entry, f"{dotted}.{entry.stem}", is_package=False)
            found = True
    init = directory / "__init__.py"
    if init.is_file():
        _register_module(project, init, dotted, is_package=True)
        return True
    if found:
        # PEP 420 namespace package: importable, but has no source of its own.
        if dotted not in project.modules:
            project.modules[dotted] = Module(dotted, directory, is_package=True)
    return found


def _skip_entry(entry: Path) -> bool:
    if entry.name.startswith("."):
        return True
    if entry.is_dir():
        return entry.name in _EXCLUDED_DIRS or not entry.name.isidentifier()
    return False


def _is_module_file(entry: Path) -> bool:
    return (
        entry.suffix == ".py" and entry.stem != "__init__" and entry.stem.isidentifier()
    )


def _register_module(
    project: Project, path: Path, name: str, *, is_package: bool
) -> None:
    # First source root wins on name collisions.
    if name in project.modules:
        return
    project.modules[name] = Module(name, path, is_package, _parse(path, project))


def _parse(path: Path, project: Project) -> ast.Module | None:
    """Parse ``path``; on failure record the error and return None.

    One bad file must never abort the analysis — the module stays in the
    index so imports of it still resolve.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        return ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError) as exc:
        project.parse_errors.append(ParseError(path, str(exc)))
        return None

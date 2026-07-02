"""Module graph structure and hand-rolled Tarjan SCC.

The graph algorithms here are implemented in-repo on purpose — do not swap
them for networkx or another library.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass

from tracegraph.discovery import Project
from tracegraph.resolver import Context, Edge, resolve_module


@dataclass(frozen=True)
class ModuleGraph:
    """Directed import graph.

    ``modules`` are the first-party nodes; edge targets outside that set are
    external leaves or unresolved placeholders and are never traversed.
    """

    modules: frozenset[str]
    edges: tuple[Edge, ...]

    def successors(
        self, contexts: Collection[Context] | None = None
    ) -> dict[str, list[str]]:
        """First-party adjacency restricted to ``contexts`` (None = all)."""
        adjacency: dict[str, list[str]] = {name: [] for name in sorted(self.modules)}
        for edge in self.edges:
            if edge.src not in self.modules or edge.dst not in self.modules:
                continue
            if contexts is not None and edge.context not in contexts:
                continue
            if edge.dst not in adjacency[edge.src]:
                adjacency[edge.src].append(edge.dst)
        return adjacency

    @property
    def external_targets(self) -> frozenset[str]:
        """stdlib and third-party leaf nodes referenced by the project."""
        return frozenset(
            edge.dst
            for edge in self.edges
            if edge.category in ("stdlib", "third_party")
        )

    @property
    def unresolved_edges(self) -> tuple[Edge, ...]:
        """Imports static analysis could not pin down — report these."""
        return tuple(edge for edge in self.edges if not edge.resolved)


def build_graph(project: Project) -> ModuleGraph:
    """Resolve every parsed module's imports into a single graph."""
    edges: list[Edge] = []
    for name in sorted(project.modules):
        module = project.modules[name]
        if module.tree is None:
            continue
        edges.extend(
            resolve_module(module.tree, module.name, module.is_package, project.index)
        )
    return ModuleGraph(modules=project.index, edges=tuple(edges))


def strongly_connected_components(
    adjacency: Mapping[str, Sequence[str]],
) -> list[list[str]]:
    """Tarjan's algorithm, iterative to survive deep import chains.

    Every successor must itself be a key of ``adjacency``. Components come
    out in reverse topological order, members sorted for determinism.
    """
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0

    def push(node: str, work: list[tuple[str, Iterator[str]]]) -> None:
        nonlocal counter
        index_of[node] = lowlink[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)
        work.append((node, iter(adjacency[node])))

    for root in adjacency:
        if root in index_of:
            continue
        work: list[tuple[str, Iterator[str]]] = []
        push(root, work)
        while work:
            node, successors = work[-1]
            advanced = False
            for succ in successors:
                if succ not in index_of:
                    push(succ, work)
                    advanced = True
                    break
                if succ in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[succ])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(sorted(component))
    return components


def load_time_cycles(graph: ModuleGraph) -> list[list[str]]:
    """Circular imports that can raise ImportError at load time.

    Only ``context == "module"`` edges participate: a cycle realized through
    a function-local or TYPE_CHECKING import does not fail at import time and
    must not be reported here. (Test impact analysis, by contrast, uses all
    edges.)

    An SCC of module edges is not enough on its own: Python tolerates cycles
    realized purely through ``binding == "module"`` edges, because a plain
    ``import x`` is satisfied by the partially initialized module object
    already in ``sys.modules``. A cycle only fails when some edge inside it
    needs names out of its target's namespace at load time, so an SCC is
    reported iff it contains an intra-SCC ``binding == "symbol"`` edge.
    (Every intra-SCC edge lies on a cycle, so one such edge suffices.)
    """
    load_time: tuple[Context, ...] = ("module",)
    adjacency = graph.successors(contexts=load_time)
    symbol_pairs = {
        (edge.src, edge.dst)
        for edge in graph.edges
        if edge.context == "module" and edge.binding == "symbol"
    }
    cycles = []
    for component in strongly_connected_components(adjacency):
        members = set(component)
        if len(component) == 1 and component[0] not in adjacency[component[0]]:
            continue
        if any(
            (src, dst) in symbol_pairs
            for src in component
            for dst in adjacency[src]
            if dst in members
        ):
            cycles.append(component)
    return cycles

# TraceGraph

Static dependency analyzer for Python codebases. TraceGraph discovers a
project's modules, parses each with `ast`, resolves import statements into a
directed module graph, then uses that graph to detect circular imports, answer
transitive-dependency queries, and select the tests affected by a file change
(test impact analysis).

Every edge carries a `context` tag — `module`, `function`, or `type_checking` —
so the analyzer can tell load-time dependencies from lazy ones: cycle detection
uses only load-time (`module`) edges, while test impact analysis uses all of
them. Each edge also carries a `binding` tag — `module` when the import only
binds a module object (plain `import x`), `symbol` when load-time code needs
names out of the target's namespace (`from x import name`, or module-scope
`x.attr` access). Python tolerates import cycles realized purely through
module-object bindings, so a cycle is reported as a load-time circular import
only when it runs through at least one `symbol` edge — the pattern that can
actually raise `ImportError` on a partially initialized module. Dynamic
imports that static analysis cannot resolve are reported as explicit blind
spots, never silently dropped.

## Usage

```bash
tracegraph analyze .                 # summary (also: --format json|dot)
tracegraph cycles .                  # load-time circular imports; exit 1 if any
tracegraph affected --changed src/pkg/mod.py .   # tests impacted by a change
```

## Development

Toolchain: Python 3.11+, [uv](https://docs.astral.sh/uv/), ruff, mypy, pytest.

```bash
uv sync              # install dependencies into .venv
uv run pytest        # run the test suite
uv run ruff check .  # lint
uv run mypy src      # type-check
```

Resolver tests are fixture-driven: each directory under `tests/fixtures/`
holds a tiny synthetic project plus an `expected_edges.json`, and the produced
edge set must match exactly. When changing the resolver, add or update a
fixture. See `CLAUDE.md` for the architecture invariants.

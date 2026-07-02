# CLAUDE.md

Guidance for coding agents working in the TraceGraph repository. Read this
before making changes. The non-obvious correctness invariants are in
**Architecture & invariants** and **Things that are easy to get wrong** —
violating those produces output that looks right but is subtly incorrect.

## Project overview

TraceGraph is a static dependency analyzer for Python codebases. It discovers
the project's modules, parses each with `ast`, resolves import statements into a
directed module graph, then uses that graph to detect circular imports, answer
transitive-dependency queries, and select the tests affected by a file change
(test impact analysis).

The substance of this project is **import resolution correctness** and the
**load-time vs. lazy edge distinction**, not the graph algorithms. Optimize
changes for resolver correctness first.

## Setup & commands

Toolchain: Python 3.11+, [uv](https://docs.astral.sh/uv/) for env/deps, ruff for
lint+format, mypy for types, pytest for tests. (If the repo uses a different
toolchain, follow what's in `pyproject.toml` and ignore this section.)

```bash
uv sync                      # install dependencies into .venv
uv run pytest                # run the full test suite
uv run pytest tests/test_resolver.py -q   # run one module
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy src              # type-check
uv run tracegraph analyze .  # run the CLI against the current dir
```

Always run `uv run ruff format .`, `uv run ruff check .`, and `uv run mypy src`
before considering a change complete. Do not introduce new lint or type errors.

## Project structure

```
src/tracegraph/
  discovery.py   # walk project, build module index, detect package roots & src/ layout
  resolver.py    # THE HEART — turn import statements into edges (cases 1–12)
  graph.py       # graph structure + hand-rolled Tarjan SCC
  analysis.py    # transitive deps (SCC condensation), reverse-reachability TIA
  report.py      # CLI text / JSON / DOT output
  cli.py         # argument parsing, command dispatch
tests/
  fixtures/      # synthetic mini-packages, one per resolver case, each with expected_edges.json
  test_resolver.py, test_graph.py, test_analysis.py, ...
```

## Architecture & invariants

These are load-bearing. Preserve them.

1. **Module granularity, not symbol granularity.** Edges connect modules, not
   functions/classes. A `from pkg import name` where `name` is a symbol (not a
   submodule) produces an edge to `pkg`, not to wherever `name` was defined.
   Don't "improve" this into symbol tracking without an explicit request — it's
   a deliberate scoping decision.

2. **Every edge carries `context`: `module` | `function` | `type_checking`,
   and `binding`: `module` | `symbol`.** Neither tag is cosmetic. `context` is
   how the analyzer distinguishes load-time dependencies from lazy ones;
   `binding` distinguishes imports satisfied by a bare module object (plain
   `import x`) from ones that need names out of the target's namespace
   (`from x import name` of a non-submodule, `from x import *`, or a plain
   import whose bound name is attribute-accessed at module scope). Never drop
   or default these fields.

3. **Cycle detection uses only `context == "module"` edges, and reports an SCC
   only if it contains an intra-SCC `binding == "symbol"` edge.** A circular
   import realized through a function-local or `TYPE_CHECKING` import does
   **not** raise `ImportError` at load time, so it must not be reported as a
   load-time cycle. Neither does a module-level cycle realized purely through
   plain `import x` statements — Python satisfies those with the partially
   initialized module object in `sys.modules` (this is why large packages like
   Flask have module-edge SCCs yet import fine). Test impact analysis, by
   contrast, uses **all** edges (a lazy import is still a real runtime
   dependency). If you touch either analysis, keep these edge sets distinct.

4. **External modules are leaf nodes.** A module is first-party iff it resolves
   into a package root (see `discovery.py`). stdlib (`sys.stdlib_module_names`)
   and third-party modules are recorded but never traversed. Keep the graph
   bounded to first-party nodes.

5. **Unresolved dynamic imports are surfaced, never silently dropped.** Literal
   `importlib.import_module("pkg.sub")` is resolved from the constant string.
   Computed names get an edge with `resolved=False` and must appear in the
   report as a known blind spot. Honesty about what static analysis can't see is
   a feature.

6. **Hand-rolled graph algorithms.** Tarjan's SCC and reachability are
   implemented in-repo on purpose — that implementation is the point of the
   project. Do **not** replace them with `networkx` or another library. The only
   acceptable third-party graph use is an optional DOT/visualization export.

## Testing

Tests are fixture-driven. `tests/fixtures/` contains tiny synthetic packages,
each exercising one resolution case, paired with an `expected_edges.json`. The
resolver tests assert the produced edge set equals the expected set exactly.

**When you change the resolver, you must add or update a fixture.** A resolver
change without a corresponding fixture is incomplete. When fixing a resolution
bug, first add a fixture that reproduces it (failing), then make it pass.

Resolution cases the fixtures must keep covering: absolute imports, relative
imports (multi-level), submodule-vs-name ambiguity, `__init__` re-exports,
namespace packages (PEP 420), `src/` layout, guarded/`TYPE_CHECKING`/in-function
imports, dynamic imports (literal and computed), aliases, first/third-party
classification, duplicate imports, unparseable files, and benign vs. failing
module-level cycles (plain-import cycles, `from`-import cycles, and
module-scope attribute use of a plainly imported module).

## Things that are easy to get wrong

- **Relative-import anchoring.** Leading dots count up from the *current
  module's package*. A package `__init__.py` anchors to the package itself; a
  regular module anchors to its containing package. Off-by-one here silently
  mis-resolves every relative import.
- **`from X import Y` resolution order.** Probe the module index for `X.Y` as a
  module *first*; only fall back to "Y is a name in X" if that misses.
- **Reverse-reachability direction for TIA.** Affected tests for a change to `C`
  are the test modules `T` such that a path `T → … → C` exists. Compute this as
  reverse-reachability from `C` on the reversed graph. Getting the direction
  backwards returns C's dependencies instead of its dependents.
- **Don't crash on one bad file.** Parse failures are recorded and skipped, not
  fatal.
- **Recall over precision for TIA.** Missing an affected test is the dangerous
  error. Don't make changes that trade away recall for a smaller selected set.

## Conventions

- Type-annotate all public functions; mypy must pass.
- Prefer stdlib (`ast`, `tomllib`, `pathlib`) over new dependencies. Adding a
  runtime dependency needs a clear justification.
- Keep `resolver.py` pure and side-effect-free: input is a parsed module +
  context, output is edges. I/O and discovery live in `discovery.py`.
- Match existing naming and docstring style in the file you're editing.

## What not to do

- Don't add `networkx` (or similar) for the core graph algorithms.
- Don't collapse the three edge contexts into a boolean or drop the tag.
- Don't silently discard unresolved dynamic imports.
- Don't widen scope to symbol-level resolution without an explicit request.
- Don't change resolver behavior without adding/updating a fixture.

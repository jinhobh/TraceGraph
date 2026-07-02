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

## Validating test impact analysis

`tracegraph affected` is validated empirically against coverage.py ground
truth. The harness runs a target project's pytest suite under `coverage run`
with one dynamic context per test (the pytest nodeid), so the coverage
database records exactly which tests executed each source module. Those
observed sets are compared with the static prediction and summarized as
precision, recall, and reduction per module:

```bash
uv run python validation/validate_tia.py                  # this repo's suite
uv run python validation/validate_tia.py /path/to/project --source pkg
```

A false negative — a test that demonstrably executed the changed module but
was not selected — is the dangerous error, and fails the run (exit 1, tunable
with `--min-recall`). Reported false positives are only an upper bound on
over-selection: per-test contexts cannot attribute import-time execution, so a
test that depends on a module purely through import side effects looks
unaffected to coverage even when selecting it is correct.

The harness has been run against an external, mid-size project with a real
suite: [Flask](https://github.com/pallets/flask) (491 tests, 24 first-party
modules with coverage). Result: **recall 1.00** — zero false negatives — with
micro precision 0.56 and mean reduction 0.08. The low reduction is a property
of Flask's suite, not resolver over-approximation: almost every test file
builds an app through a handful of shared `conftest.py` fixtures that import
most of `flask`'s public surface, so nearly the whole module graph is
legitimately reachable from nearly every test module. (Click was the other
candidate considered; Flask's larger, fixture-heavy suite is the more
demanding case.) This isn't an artifact of scoring at test-module instead of
test-function granularity, despite Flask's file sizes ranging from 1 test to
132: reweighting the same predictions by actual test-function count barely
moves the mean (0.078 either way), because for 22 of 24 source modules the
prediction already spans all 22 of 22 test modules — there's no small-file/
large-file selection bias for a finer denominator to expose.

A second external run targets [requests](https://github.com/psf/requests)
(635 tests across 9 test modules, 19 first-party modules with coverage),
picked as the contrasting case: no shared app-building fixture pulls in the
whole package the way Flask's `conftest.py` does, so most test modules cover
only the source modules their name suggests (`test_adapters.py`,
`test_cookies.py`, `test_structures.py`, ...). Result: **recall 1.00** — zero
false negatives — with micro precision 0.51 and mean reduction 0.34, over 4x
Flask's 0.08 — consistent with a suite that doesn't route every test through
a handful of shared fixtures. Several leaf modules (`requests.certs`,
`requests.compat`, `requests.status_codes`, `requests.__version__`,
`requests.packages`) show zero measured truth despite being imported
everywhere: coverage only attributes a line to a test's context the first
time it executes under the default (import-time) context, so a module
touched solely as a side effect of importing `requests` records no
test-specific coverage. Every prediction for those modules therefore scores
as a false positive, which is the false-positive-as-upper-bound caveat above,
not a resolver defect — `tracegraph cycles` and `tracegraph analyze` on this
same checkout show the edges into those modules resolving correctly.

On TraceGraph's own suite — much smaller and, by construction, unusually
decoupled — the harness measures precision 1.00, recall 1.00, and mean
reduction 0.51. Treat this as a negative control: it shows the harness and
resolver behave correctly when there's no import noise to obscure a mistake,
not that TIA meaningfully narrows down suites in general — Flask and requests
above are the representative external numbers, spanning tight and loose
architectures respectively.

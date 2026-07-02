"""TraceGraph: static import-graph analysis for Python projects.

Discovers a project's modules, resolves import statements into a directed
module graph, detects load-time circular imports, and selects the tests
affected by a file change.
"""

from tracegraph.resolver import Edge

__version__ = "0.1.0"

__all__ = ["Edge", "__version__"]

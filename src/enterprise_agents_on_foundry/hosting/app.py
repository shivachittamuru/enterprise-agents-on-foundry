"""The Hosted Agent runtime entry point.

Assembles the real settings, database client, and model into the production
dependencies, wraps the graph in the Responses adapter, and starts the Foundry
host. Kept thin: all mapping lives in
:mod:`enterprise_agents_on_foundry.hosting.responses`.

Run locally with ``uv run eaof-host`` after ``uv sync --extra hosting`` and
``az login``. The host serves ``POST /responses`` on ``PORT`` (default 8088).
"""

from __future__ import annotations

import os
from typing import Any

from enterprise_agents_on_foundry.agents.nodes import production_dependencies
from enterprise_agents_on_foundry.config.settings import load_settings
from enterprise_agents_on_foundry.database.connection import connect
from enterprise_agents_on_foundry.hosting.responses import build_adapter_graph

DEFAULT_PORT = 8088


def build_host() -> Any:
    """Build the Responses host from the process environment.

    The hosting SDK is an optional extra, so it is imported here rather than at
    module load, which keeps the adapter importable and testable without it.
    """
    from langchain_azure_ai.agents.hosting import ResponsesHostServer

    settings = load_settings()
    client = connect(settings)
    deps = production_dependencies(settings, client)
    graph = build_adapter_graph(deps)
    return ResponsesHostServer(graph)


def main() -> None:
    """Start the Responses host on the configured port."""
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    build_host().run(port=port)


if __name__ == "__main__":
    main()

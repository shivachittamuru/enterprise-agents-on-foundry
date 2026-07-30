"""The Text-to-SQL agent boundary.

This is the sixth named boundary in the package, alongside ``config``,
``infrastructure``, ``database``, ``observability``, and ``cli``. It owns the
graph, its state, and the contracts the model is held to.

Nothing here is re-exported at the package root. An import always names the
boundary it crossed.
"""

from __future__ import annotations

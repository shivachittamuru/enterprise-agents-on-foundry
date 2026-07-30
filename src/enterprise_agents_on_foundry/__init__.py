"""Enterprise agents on Microsoft Foundry: progressive learning laboratory.

The package is organised by boundary rather than by release:

* :mod:`~enterprise_agents_on_foundry.config` is the only place the environment
  is read.
* :mod:`~enterprise_agents_on_foundry.infrastructure` answers questions about an
  Azure environment that already exists.
* :mod:`~enterprise_agents_on_foundry.database` is the only place Azure SQL is
  touched.
* :mod:`~enterprise_agents_on_foundry.observability` measures how long things
  take.
* :mod:`~enterprise_agents_on_foundry.cli` adapts the above to a terminal.

Subpackages are imported explicitly rather than re-exported here, so that reading
an import tells you which boundary a caller depends on.
"""

__all__ = ["__version__"]

__version__ = "0.3.0"

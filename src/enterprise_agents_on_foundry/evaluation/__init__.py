"""Deterministic evaluation of the Text-to-SQL agent.

v0.4 starts with the contract rather than the runner: what a case asserts
(:mod:`~enterprise_agents_on_foundry.evaluation.cases`), what a graded case
records (:mod:`~enterprise_agents_on_foundry.evaluation.results`), and how a
dataset is loaded and selected
(:mod:`~enterprise_agents_on_foundry.evaluation.dataset`).

Nothing here calls the graph, a model, or Azure SQL. Every assertion is decidable
from evidence the agent already returns.
"""

__all__: list[str] = []

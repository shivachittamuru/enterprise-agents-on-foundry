"""Render the database schema as text for the model.

The legacy agent asked the model to call ``list_tables`` and ``get_schema``
tools, so every question paid for at least two extra model turns before any SQL
was drafted, and a model that skipped the tools wrote SQL against a schema it
had guessed.

AdventureWorksLT is small enough that the whole schema fits comfortably in one
prompt. Loading it once removes both the extra turns and the guessing, and it
removes the model's ability to choose which parts of the database to look at.

Retrieval over the schema is deliberately absent. It only becomes worthwhile on
a database too large to render, which this is not.
"""

from __future__ import annotations

from typing import Final

from enterprise_agents_on_foundry.database.connection import DatabaseClient
from enterprise_agents_on_foundry.database.metadata import load_query
from enterprise_agents_on_foundry.database.models import QueryResult

SCHEMA_CONTEXT_QUERY: Final = "04_schema_context"

_MAX_SCHEMA_ROWS: Final = 5_000
"""Upper bound on rendered columns.

AdventureWorksLT has roughly 190. The cap exists so that pointing this code at a
larger database fails visibly instead of silently truncating the schema the
model reasons about.
"""

__all__ = ["SCHEMA_CONTEXT_QUERY", "load_schema_context", "render_schema_context"]


def load_schema_context(client: DatabaseClient) -> str:
    """Read the schema through the database boundary and render it as text."""
    result = client.execute_sql(
        load_query(SCHEMA_CONTEXT_QUERY),
        max_rows=_MAX_SCHEMA_ROWS,
        label=SCHEMA_CONTEXT_QUERY,
    )
    return render_schema_context(result)


def render_schema_context(result: QueryResult) -> str:
    """Format schema rows as one indented block per table.

    Column type, nullability, primary keys, and foreign key targets are all
    included, because those are what the model gets wrong when it has to infer
    join conditions from names alone.
    """
    lines: list[str] = []
    current_table = ""

    for row in result.as_dicts():
        table = f"{row['schema_name']}.{row['table_name']}"
        if table != current_table:
            if current_table:
                lines.append("")
            lines.append(table)
            current_table = table
        lines.append(f"  {_render_column(row)}")

    if result.truncated:
        lines.append("")
        lines.append("-- schema truncated; the model is not seeing every table")

    return "\n".join(lines)


def _render_column(row: dict[str, object]) -> str:
    """Render one column as a single line."""
    parts = [f"{row['column_name']} {row['data_type']}"]
    if not _as_bool(row["is_nullable"]):
        parts.append("not null")
    if _as_bool(row["is_primary_key"]):
        parts.append("primary key")

    line = " ".join(parts)
    reference = row["references_column"]
    if reference:
        line = f"{line} -> {reference}"
    return line


def _as_bool(value: object) -> bool:
    """Interpret the driver's representation of a bit column.

    pyodbc returns ``bit`` as ``bool`` and a ``CASE`` expression as ``int``, and
    both appear in the schema query.
    """
    return bool(value)

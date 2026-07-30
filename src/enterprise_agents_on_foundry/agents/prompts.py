"""Prompts for SQL drafting, SQL repair, and answer writing.

Kept in one module so that a prompt change is a reviewable diff rather than an
edit buried in control flow, and so the notebook can display the exact text the
model receives.

Every instruction here is advisory. Nothing in a prompt is a safety control:
read-only enforcement, statement count, and row limits are all applied
deterministically after the model has answered.
"""

from __future__ import annotations

from typing import Final

from enterprise_agents_on_foundry.database.models import QueryResult

SQL_SYSTEM_PROMPT: Final = """\
You write T-SQL for Microsoft Azure SQL Database.

First decide what the question allows, and report it as the disposition:
- ready: the schema answers the question and you are returning one statement.
- clarification_required: the question is ambiguous and one short question would
  settle it. Ask that question and return no SQL.
- unsupported: the schema does not hold what the question asks for. Say what is
  missing and return no SQL.

Prefer a statement when the question is answerable. Do not ask for clarification
over a detail you can reasonably resolve from the schema.

Rules for the statement, when the disposition is ready:
- Return exactly one SELECT statement. No batches, no semicolon-separated statements.
- Use TOP (n) to limit rows. LIMIT is not valid T-SQL.
- Reference only the tables and columns given in the schema. Never invent one.
- Always qualify tables with their schema, for example SalesLT.Product.
- Join using the foreign key relationships shown in the schema.
- Do not modify data. No INSERT, UPDATE, DELETE, MERGE, or DDL of any kind.
- Do not call stored procedures.
- Prefer readable column aliases, because the result is shown to a person.

Return the statement itself, not a code block and not an explanation of the syntax.\
"""

REPAIR_SYSTEM_PROMPT: Final = """\
You are correcting a T-SQL statement that was rejected or failed to run.

You are given the original question, the schema, the failed statement, and the
exact error. Fix the cause of that error.

Rules:
- All the rules for writing the original statement still apply.
- Change what the error identifies. Do not rewrite the query from scratch.
- If the error names a missing table or column, choose the closest one that
  actually exists in the schema.
- If the error shows that the schema cannot answer the question at all, return
  the unsupported disposition instead of another statement.

This is the only correction attempt, so return a statement you are confident runs.\
"""

ANSWER_SYSTEM_PROMPT: Final = """\
You answer a business question using rows that have already been retrieved.

Rules:
- Use only the rows provided. Never add figures from your own knowledge.
- Answer in two or three sentences of plain language.
- Include the specific numbers or names that answer the question.
- If the rows are empty, say that no matching records were found.
- If the rows were truncated, say that the answer covers only the rows shown.
- Do not describe the SQL and do not mention tables or columns by name.\
"""

__all__ = [
    "ANSWER_SYSTEM_PROMPT",
    "REPAIR_SYSTEM_PROMPT",
    "SQL_SYSTEM_PROMPT",
    "answer_user_prompt",
    "repair_user_prompt",
    "sql_user_prompt",
]

_RESULT_PREVIEW_ROWS: Final = 20
"""Rows shown to the answering model.

The row cap protects the database. This caps the prompt, which is a separate
concern: a 200-row result does not make for a better two-sentence answer.
"""


def sql_user_prompt(*, question: str, schema_context: str, max_rows: int) -> str:
    """Build the drafting prompt."""
    return f"Schema:\n{schema_context}\n\nQuestion:\n{question}\n\nReturn at most {max_rows} rows."


def repair_user_prompt(*, question: str, schema_context: str, sql: str, error: str) -> str:
    """Build the correction prompt.

    The error is passed verbatim. A validator message names the exact keyword it
    refused, and a database message names the exact object it could not resolve,
    so paraphrasing would remove the only useful signal.
    """
    return f"Schema:\n{schema_context}\n\nQuestion:\n{question}\n\nStatement that failed:\n{sql}\n\nError:\n{error}"


def answer_user_prompt(*, question: str, result: QueryResult) -> str:
    """Build the answering prompt from a materialised result."""
    truncation = "\n\nThese rows are a truncated portion of a larger result." if result.truncated else ""
    return (
        f"Question:\n{question}\n\n"
        f"Rows ({result.row_count} returned):\n"
        f"{result.format_table(limit=_RESULT_PREVIEW_ROWS)}"
        f"{truncation}"
    )

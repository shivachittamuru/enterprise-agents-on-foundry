# Repository instructions

This repository is a notebook-first learning project for building enterprise agents on Microsoft Foundry.

* Use Python 3.12, `uv`, a `src/` package layout, Ruff, mypy, and pytest.
* Prefer the smallest readable implementation that satisfies the current release. Do not add speculative abstractions, placeholder modules, or future-release capabilities.
* Keep reusable behavior under `src/`. Keep scripts thin. Notebooks must import production code rather than duplicate it.
* Use typed interfaces, explicit state, focused errors, and centralized settings. Do not scatter environment-variable access across modules.
* Preserve Microsoft Entra authentication and read-only Azure SQL access. Never log secrets, credentials, tokens, or connection strings.
* Treat model output as untrusted. Validate SQL deterministically before execution and reject mutations.
* Add focused unit tests for new behavior. Keep live Azure/model tests separately marked as integration tests.
* Do not modify unrelated files or infrastructure unless the task explicitly requires it.
* Before implementing, inspect the existing code and reuse established project boundaries.
* After each bounded task, report files changed, commands run, checks passed, and unresolved issues.

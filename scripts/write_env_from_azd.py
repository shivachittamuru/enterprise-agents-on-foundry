"""Write provisioning outputs into a local ``.env`` file.

Runs after ``azd provision``. Only keys that already exist in ``.env.example``
are written, so a stray azd variable cannot silently introduce new configuration.
Existing unrelated lines and comments are preserved, and protected keys are never
overwritten.

Key names are printed. Values are not.

Usage:
    uv run python scripts/write_env_from_azd.py
"""

from __future__ import annotations

import sys

from enterprise_agents_on_foundry.cli.console import EXIT_FAILED, EXIT_OK
from enterprise_agents_on_foundry.config.settings import repository_root
from enterprise_agents_on_foundry.infrastructure.azd_outputs import (
    PROTECTED_KEYS,
    declared_env_keys,
    load_azd_outputs,
    write_env_file,
)


def main(argv: list[str] | None = None) -> int:
    """Project azd outputs onto ``.env`` and return a process exit code."""
    if argv:
        print(f"write_env_from_azd.py takes no arguments; received: {' '.join(argv)}")
        return EXIT_FAILED

    root = repository_root()
    example_path = root / ".env.example"
    env_path = root / ".env"

    if not example_path.is_file():
        print(f"Cannot find {example_path}. Nothing was written.")
        return EXIT_FAILED

    outputs = load_azd_outputs()
    if outputs.is_empty:
        print("No azd environment values were found. Run 'azd provision' first.")
        return EXIT_FAILED

    allowed = [key for key in declared_env_keys(example_path) if key not in PROTECTED_KEYS]
    updates = {key: value for key in allowed if (value := outputs.get(key))}

    if not env_path.is_file():
        env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Created {env_path.name} from .env.example.")

    changed = write_env_file(env_path, updates)

    print(f"Updated {len(changed)} key(s) in {env_path.name}.")
    for key in changed:
        print(f"  {key}")

    ignored = sorted(set(outputs.names()) - set(allowed))
    if ignored:
        print(f"Ignored {len(ignored)} azd value(s) not declared in .env.example.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

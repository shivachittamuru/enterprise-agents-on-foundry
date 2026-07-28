"""Write provisioning outputs into a local ``.env`` file.

Runs after ``azd provision``. Only keys that already exist in ``.env.example``
are written, so a stray azd variable cannot silently introduce new configuration.
Existing unrelated lines and comments are preserved.

Usage:
    uv run python scripts/write_env_from_azd.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from enterprise_agents_on_foundry.setup.azure_env import load_azd_env_values
from enterprise_agents_on_foundry.setup.config import repository_root

# Values that are secrets or that a developer sets by hand are never overwritten.
PROTECTED_KEYS = frozenset({"ALLOW_DATABASE_BOOTSTRAP", "APPLICATIONINSIGHTS_CONNECTION_STRING"})


def known_keys(example_path: Path) -> list[str]:
    """Return the ordered keys declared in ``.env.example``."""
    keys: list[str] = []
    for line in example_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, _ = stripped.partition("=")
        if separator:
            keys.append(key.strip())
    return keys


def apply_values(env_path: Path, updates: dict[str, str]) -> list[str]:
    """Update or append ``updates`` in ``env_path`` and return the changed keys."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    changed: list[str] = []
    seen: set[str] = set()

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, current = stripped.partition("=")
        key = key.strip()
        if not separator or key not in updates:
            continue
        seen.add(key)
        if current.strip() != updates[key]:
            lines[index] = f"{key}={updates[key]}"
            changed.append(key)

    missing = [key for key in updates if key not in seen]
    if missing:
        lines.append("")
        lines.append("# Appended from azd outputs.")
        for key in missing:
            lines.append(f"{key}={updates[key]}")
            changed.append(key)

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def main() -> int:
    root = repository_root()
    example_path = root / ".env.example"
    env_path = root / ".env"

    if not example_path.is_file():
        print(f"Cannot find {example_path}. Nothing was written.")
        return 1

    azd_values = load_azd_env_values()
    if not azd_values:
        print("No azd environment values were found. Run 'azd provision' first.")
        return 1

    allowed = [key for key in known_keys(example_path) if key not in PROTECTED_KEYS]
    updates = {key: azd_values[key] for key in allowed if azd_values.get(key)}

    if not env_path.is_file():
        env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Created {env_path.name} from .env.example.")

    changed = apply_values(env_path, updates)

    ignored = sorted(set(azd_values) - set(allowed))
    print(f"Updated {len(changed)} key(s) in {env_path.name}.")
    for key in changed:
        print(f"  {key}")
    if ignored:
        print(f"Ignored {len(ignored)} azd value(s) not declared in .env.example.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

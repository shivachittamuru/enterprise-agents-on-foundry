"""Model catalog and quota checks.

The v0.1 decision record asked for a specific model, and the catalog in the
target region did not contain it. Discovering that during a deployment produces
an opaque failure several minutes in, so the check runs first and explains what
is actually available.

There is no fallback logic anywhere in this module. A model that cannot be
deployed stops provisioning with an explicit message rather than being silently
substituted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from enterprise_agents_on_foundry.infrastructure.commands import run_json_command

_CATALOG_TIMEOUT_SECONDS: Final = 180

__all__ = [
    "CatalogModel",
    "ModelAvailability",
    "check_model_availability",
    "get_quota",
    "list_catalog_models",
]


@dataclass(frozen=True, slots=True)
class CatalogModel:
    """One model as published in a region."""

    name: str
    version: str
    format: str
    skus: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelAvailability:
    """Whether a requested model, version, and SKU can be deployed."""

    requested_name: str
    requested_version: str
    requested_sku: str
    location: str
    name_found: bool
    version_found: bool
    sku_found: bool
    quota_limit: float | None
    quota_used: float | None
    requested_capacity: int
    alternatives: tuple[CatalogModel, ...]

    @property
    def ok(self) -> bool:
        """True when the model can be deployed at the requested capacity."""
        return self.name_found and self.version_found and self.sku_found and self.quota_sufficient

    @property
    def quota_sufficient(self) -> bool:
        """True when remaining quota covers the requested capacity."""
        if self.quota_limit is None:
            return False
        used = self.quota_used or 0.0
        return (self.quota_limit - used) >= self.requested_capacity

    @property
    def remaining_quota(self) -> float:
        """Quota left in thousands of tokens per minute."""
        return (self.quota_limit or 0.0) - (self.quota_used or 0.0)

    def failure_reason(self) -> str | None:
        """Explain the first blocking problem, or return None when deployable."""
        if not self.name_found:
            names = ", ".join(sorted({model.name for model in self.alternatives})) or "none"
            return (
                f"Model '{self.requested_name}' is not published in {self.location} for this "
                f"subscription. Comparable models available in this region: {names}. "
                f"Set AZURE_MODEL_NAME and AZURE_MODEL_VERSION to one of these, or choose "
                f"another region. No substitution is made automatically."
            )
        if not self.version_found:
            versions = ", ".join(sorted({m.version for m in self.alternatives if m.name == self.requested_name}))
            return (
                f"Model '{self.requested_name}' exists in {self.location} but version "
                f"'{self.requested_version}' does not. Published versions: {versions or 'none'}."
            )
        if not self.sku_found:
            skus = ", ".join(
                sorted(
                    {
                        sku
                        for m in self.alternatives
                        if m.name == self.requested_name and m.version == self.requested_version
                        for sku in m.skus
                    }
                )
            )
            return (
                f"SKU '{self.requested_sku}' is not offered for {self.requested_name} "
                f"{self.requested_version} in {self.location}. Offered SKUs: {skus or 'none'}."
            )
        if self.quota_limit is None:
            return (
                f"No quota entry was found for {self.requested_sku}.{self.requested_name} in "
                f"{self.location}. Request quota before provisioning."
            )
        if not self.quota_sufficient:
            return (
                f"Insufficient quota for {self.requested_name} in {self.location}: "
                f"{self.remaining_quota:g}K TPM remaining, {self.requested_capacity}K TPM requested."
            )
        return None


def list_catalog_models(location: str) -> tuple[CatalogModel, ...]:
    """Return the models published in ``location`` for the current subscription."""
    payload = run_json_command(
        ["az", "cognitiveservices", "model", "list", "--location", location, "--output", "json"],
        timeout_seconds=_CATALOG_TIMEOUT_SECONDS,
    )
    if not isinstance(payload, list):
        return ()

    seen: dict[tuple[str, str], CatalogModel] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        model = entry.get("model")
        if not isinstance(model, dict):
            continue
        name = str(model.get("name", ""))
        version = str(model.get("version", ""))
        if not name:
            continue
        skus = tuple(
            str(sku.get("name", "")) for sku in model.get("skus", []) if isinstance(sku, dict) and sku.get("name")
        )
        key = (name, version)
        if key in seen:
            merged = tuple(sorted(set(seen[key].skus) | set(skus)))
            seen[key] = CatalogModel(name=name, version=version, format=seen[key].format, skus=merged)
        else:
            seen[key] = CatalogModel(
                name=name,
                version=version,
                format=str(model.get("format", "")),
                skus=tuple(sorted(set(skus))),
            )
    return tuple(seen.values())


def get_quota(location: str, sku_name: str, model_name: str) -> tuple[float | None, float | None]:
    """Return ``(limit, used)`` for a model quota, in thousands of tokens per minute."""
    payload = run_json_command(
        ["az", "cognitiveservices", "usage", "list", "--location", location, "--output", "json"],
        timeout_seconds=_CATALOG_TIMEOUT_SECONDS,
    )
    if not isinstance(payload, list):
        return None, None

    target = f"{sku_name}.{model_name}".lower()
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        value = str(name.get("value", "")) if isinstance(name, dict) else ""
        if value.lower().endswith(target):
            limit = entry.get("limit")
            used = entry.get("currentValue")
            return (
                float(limit) if isinstance(limit, int | float) else None,
                float(used) if isinstance(used, int | float) else None,
            )
    return None, None


def check_model_availability(
    *,
    location: str,
    model_name: str,
    model_version: str,
    sku_name: str = "GlobalStandard",
    capacity: int = 10,
) -> ModelAvailability:
    """Check whether a model can be deployed as configured."""
    catalog = list_catalog_models(location)

    by_name = [model for model in catalog if model.name == model_name]
    by_version = [model for model in by_name if model.version == model_version]
    sku_found = any(sku_name in model.skus for model in by_version)

    quota_limit, quota_used = (None, None)
    if by_version and sku_found:
        quota_limit, quota_used = get_quota(location, sku_name, model_name)

    # When the model is missing, surface only chat-capable alternatives rather
    # than the entire catalog, which runs to hundreds of entries.
    alternatives = (
        catalog
        if by_name
        else tuple(model for model in catalog if "gpt" in model.name.lower() and "image" not in model.name.lower())
    )

    return ModelAvailability(
        requested_name=model_name,
        requested_version=model_version,
        requested_sku=sku_name,
        location=location,
        name_found=bool(by_name),
        version_found=bool(by_version),
        sku_found=sku_found,
        quota_limit=quota_limit,
        quota_used=quota_used,
        requested_capacity=capacity,
        alternatives=alternatives,
    )

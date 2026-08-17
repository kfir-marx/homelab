from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from .ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter, PublicAtsAdapter


class RegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str
    ats: str
    slug: str
    enabled: bool = False


class CompanyRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    companies: list[RegistryEntry]


def load_adapters(path: Path) -> list[PublicAtsAdapter]:
    registry = CompanyRegistry.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    kinds = {"greenhouse": GreenhouseAdapter, "lever": LeverAdapter, "ashby": AshbyAdapter}
    adapters: list[PublicAtsAdapter] = []
    for entry in registry.companies:
        if not entry.enabled:
            continue
        try:
            adapter = kinds[entry.ats.casefold()]
        except KeyError as exc:
            raise ValueError(f"unsupported ATS type: {entry.ats}") from exc
        adapters.append(adapter(entry.company, entry.slug))
    return adapters

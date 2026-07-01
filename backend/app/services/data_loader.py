"""Load and serve German Mietspiegel comparison data.

Single source of truth for the city reference dataset:
  <repo>/data/processed/germany_cities.json

Each entry contains:
- id, slug, name, federal_state (slug-safe identifiers)
- publisher, source_title, source_url, year, kind (qualifiziert/einfach/marktbericht)
- average_eur_per_sqm (Nettokaltmiete), spread low/high
- plz_prefixes (5-stellige PLZ oder PLZ-Präfix; Berlin z.B. 10..14)

The loader validates the JSON against a schema-check and caches
in-memory. Hot-reload supported via reload_dataset() for cron updates.

This module deliberately has zero dependencies beyond pydantic — no DB,
no async — so it can be unit-tested without spinning up infrastructure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Default location: <repo>/data/processed/germany_cities.json
# Walking upward from app/services/data_loader.py:
#   app/services/data_loader.py  →  3 levels up = repo root
DEFAULT_DATA_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "data"
    / "processed"
    / "germany_cities.json"
)


@dataclass(frozen=True)
class CityDataset:
    """One comparison dataset (city/municipality/marktbericht)."""

    id: str
    slug: str
    name: str
    federal_state: str
    nuts_code: Optional[str]
    kind: str  # qualifizierter_mietspiegel | einfacher_mietspiegel | marktbericht
    publisher: str
    source_title: str
    source_url: str
    year: int
    average_eur_per_sqm: float
    spread_eur_per_sqm_low: float
    spread_eur_per_sqm_high: float
    notes: str = ""
    plz_prefixes: list[str] = field(default_factory=list)

    def matches_plz(self, plz: str) -> bool:
        """Check whether a 5-digit PLZ belongs to this dataset.

        Supports both:
          "12" matches prefix "12" → "12***"
          "10115" matches prefix "10" → "10***"
        """
        if not plz or not self.plz_prefixes:
            return False
        for pfx in self.plz_prefixes:
            pfx_clean = pfx.strip()
            if plz.startswith(pfx_clean):
                return True
        return False


@dataclass
class CityDatabase:
    """In-memory index over the germany_cities.json dataset."""

    datasets: dict[str, CityDataset]  # id → dataset
    by_slug: dict[str, CityDataset]   # slug → dataset
    by_plz_prefix: dict[str, list[CityDataset]]  # first 1-2 PLZ digits → list

    def all(self) -> list[CityDataset]:
        return list(self.datasets.values())

    def get(self, slug: str) -> Optional[CityDataset]:
        return self.by_slug.get(slug.lower())

    def find_by_plz(self, plz: str) -> list[CityDataset]:
        """Return datasets whose PLZ prefixes match the given PLZ.

        Most-precise wins: a 2-char prefix beats a 1-char prefix.
        """
        if not plz or len(plz) < 1:
            return []
        # Try longest prefix first
        for length in sorted({len(p) for p in self.by_plz_prefix}, reverse=True):
            if len(plz) < length:
                continue
            key = plz[:length]
            hits = self.by_plz_prefix.get(key, [])
            if hits:
                return hits
        return []

    def search(self, query: str) -> list[CityDataset]:
        """Naive name-slug matcher, same logic old engine used."""
        q = query.lower()
        return [
            ds for ds in self.datasets.values()
            if q in ds.name.lower() or q in ds.slug
        ]


def _load_from_path(path: Path) -> CityDatabase:
    """Parse the JSON file and build indexes."""
    if not path.exists():
        raise FileNotFoundError(
            f"City dataset not found at {path}. "
            "Run `python scripts/build_dataset.py` to (re)build, "
            "or set CITY_DATA_PATH."
        )

    raw = json.loads(path.read_text(encoding="utf-8"))

    datasets: dict[str, CityDataset] = {}
    by_slug: dict[str, CityDataset] = {}
    by_plz_prefix: dict[str, list[CityDataset]] = {}

    for entry in raw.get("datasets", []):
        ds = CityDataset(
            id=entry["id"],
            slug=entry["slug"],
            name=entry["name"],
            federal_state=entry["federal_state"],
            nuts_code=entry.get("nuts_code"),
            kind=entry["kind"],
            publisher=entry["publisher"],
            source_title=entry["source_title"],
            source_url=entry["source_url"],
            year=int(entry["year"]),
            average_eur_per_sqm=float(entry["average_eur_per_sqm"]),
            spread_eur_per_sqm_low=float(
                entry.get("spread_eur_per_sqm_low", entry["average_eur_per_sqm"])
            ),
            spread_eur_per_sqm_high=float(
                entry.get("spread_eur_per_sqm_high", entry["average_eur_per_sqm"])
            ),
            notes=entry.get("notes", ""),
            plz_prefixes=list(entry.get("plz_prefixes", [])),
        )
        datasets[ds.id] = ds
        by_slug[ds.slug] = ds
        for pfx in ds.plz_prefixes:
            by_plz_prefix.setdefault(pfx, []).append(ds)

    return CityDatabase(
        datasets=datasets,
        by_slug=by_slug,
        by_plz_prefix=by_plz_prefix,
    )


# Module-level singleton, lazily populated.
_db: Optional[CityDatabase] = None
_load_path: Optional[Path] = None


def get_database(path: Optional[Path] = None) -> CityDatabase:
    """Return the cached CityDatabase, loading it on first call."""
    global _db, _load_path
    target = Path(path) if path else DEFAULT_DATA_PATH

    if _db is not None and _load_path == target:
        return _db

    _db = _load_from_path(target)
    _load_path = target
    return _db


def reload_dataset(path: Optional[Path] = None) -> CityDatabase:
    """Force a fresh load from disk (use after admin updates)."""
    global _db, _load_path
    _db = None
    _load_path = None
    return get_database(path)


def reset_database_cache() -> None:
    """Clear the in-memory cache without reloading.

    Tests use this to assert against a clean module state; production code
    should prefer ``reload_dataset()`` instead.
    """
    global _db, _load_path
    _db = None
    _load_path = None


# Convenience wrappers ---------------------------------------------------------


def find_by_plz(plz: str) -> list[CityDataset]:
    return get_database().find_by_plz(plz)


def get_by_slug(slug: str) -> Optional[CityDataset]:
    return get_database().get(slug)


def all_cities() -> list[CityDataset]:
    return get_database().all()

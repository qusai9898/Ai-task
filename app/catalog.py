"""Recipe catalog loading from CSV."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field


class Recipe(BaseModel):
    """A single row from recipe_catalog.csv."""

    recipe_code: str
    recipe_name: str
    category: str
    unit: str
    material_cost_sar: Decimal
    labour_hours_per_unit: Decimal
    labour_rate_sar_per_hr: Decimal
    equipment_cost_sar: Decimal
    min_order_qty: Decimal
    standard_margin_pct: Decimal
    notes: str = ""

    @property
    def unit_cost_sar(self) -> Decimal:
        """Deterministic internal cost per catalog unit (before margin)."""

        labour_cost = self.labour_hours_per_unit * self.labour_rate_sar_per_hr
        return self.material_cost_sar + labour_cost + self.equipment_cost_sar

    @property
    def unit_price_sar(self) -> Decimal:
        """Client sell price per unit using catalog margin percentage."""

        margin_fraction = self.standard_margin_pct / Decimal("100")
        if margin_fraction >= Decimal("1"):
            raise ValueError(f"Invalid margin for {self.recipe_code}: {self.standard_margin_pct}%")
        return self.unit_cost_sar / (Decimal("1") - margin_fraction)


class RecipeCatalog:
    """In-memory recipe catalog."""

    def __init__(self, recipes: dict[str, Recipe]) -> None:
        self._recipes = recipes

    @classmethod
    def load(cls, path: Path | str) -> RecipeCatalog:
        catalog_path = Path(path)
        recipes: dict[str, Recipe] = {}

        with catalog_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                recipe = Recipe(
                    recipe_code=row["recipe_code"].strip(),
                    recipe_name=row["recipe_name"].strip(),
                    category=row["category"].strip(),
                    unit=row["unit"].strip(),
                    material_cost_sar=Decimal(row["material_cost_sar"]),
                    labour_hours_per_unit=Decimal(row["labour_hours_per_unit"]),
                    labour_rate_sar_per_hr=Decimal(row["labour_rate_sar_per_hr"]),
                    equipment_cost_sar=Decimal(row["equipment_cost_sar"]),
                    min_order_qty=Decimal(row["min_order_qty"]),
                    standard_margin_pct=Decimal(row["standard_margin_pct"]),
                    notes=row.get("notes", "").strip(),
                )
                recipes[recipe.recipe_code] = recipe

        return cls(recipes)

    def get(self, recipe_code: str) -> Recipe | None:
        return self._recipes.get(recipe_code)

    def all(self) -> list[Recipe]:
        return list(self._recipes.values())

    def codes(self) -> set[str]:
        return set(self._recipes.keys())

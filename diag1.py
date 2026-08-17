"""
Phase 1 diagnostic: reproduce the exact current quote output for the full Nexus fixture.
Print every quote line AND every global_requirement in the extraction to expose root causes.
"""
import sys
sys.path.insert(0, '.')
from tests.fixtures import build_full_nexus_brief_extraction
from app.quote_generator import QuoteGenerator
from pathlib import Path

extraction = build_full_nexus_brief_extraction()
gen = QuoteGenerator.from_catalog_path('data/recipe_catalog.csv')
quote = gen.generate(extraction)

print("=== GLOBAL REQUIREMENTS IN EXTRACTION ===")
for req in extraction.global_requirements:
    print(f"  [{req.requirement_type.value:25s}] mandatory={str(req.is_mandatory):5s} | {req.observation_id:30s} | {req.description}")

print()
print("=== ALL QUOTE LINES (with item_id) ===")
for line in quote.lines:
    qty_s = ''
    if line.quantity is not None:
        qty_s = f'{line.quantity} {line.unit}'
    elif line.min_quantity is not None:
        qty_s = f'{line.min_quantity}-{line.max_quantity} {line.unit}'
    else:
        qty_s = f'NO_QTY {line.unit}'
    total = line.line_total_sar
    if total is None and line.min_line_total_sar:
        total = f'{line.min_line_total_sar:.0f}-{line.max_line_total_sar:.0f}'
    print(f"  [{line.status.value:15s}] id={line.item_id or '':35s} | {qty_s:22s} | SAR {total}")
    print(f"    desc={line.description}")

print()
print("=== BREAKOUT ITEMS IN EXTRACTION ===")
for item in extraction.items:
    if 'breakout' in item.item_id:
        print(f"  {item.item_id}: {item.label} | loc={item.location}")
        for d in item.descriptions:
            print(f"    desc: {d.client_text}")
        for q in item.quantities:
            print(f"    qty:  {q.quantity.raw_text}")
        for r in item.requirements:
            print(f"    req:  {r.description}")

print()
print("=== AV-PROJ-15K catalog entry ===")
from app.catalog import RecipeCatalog
cat = RecipeCatalog.load('data/recipe_catalog.csv')
r = cat.get('AV-PROJ-15K')
print(f"  unit={r.unit} min_order_qty={r.min_order_qty} notes={r.notes}")

print()
print("=== FRN-CTL-HIGH catalog entry ===")
r2 = cat.get('FRN-CTL-HIGH')
print(f"  unit={r2.unit} min_order_qty={r2.min_order_qty} notes={r2.notes}")

print()
print("=== MICROPHONE search in catalog ===")
for code in cat.codes():
    rec = cat.get(code)
    if any(kw in rec.recipe_name.lower() for kw in ('mic', 'microphone', 'sound', 'pa')):
        print(f"  {code}: {rec.recipe_name} | notes: {rec.notes}")

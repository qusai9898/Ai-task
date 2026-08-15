# AI Quoting Tool

AI-powered quoting tool for an events production company. The pipeline reads a client brief (PDF email thread), extracts structured observations with OpenAI, matches items to a recipe catalog, calculates quantities and prices deterministically, and supports human review via a Streamlit UI.

## Architecture

```
PDF brief
  → text extraction (pypdf)
  → LLM structured extraction (OpenAI Responses API → BriefExtraction)
  → catalog matching (deterministic rules)
  → quantity calculation (deterministic)
  → pricing (catalog costs + margins)
  → human review / resolution (Streamlit)
  → final structured quote (JSON)
```

**Important:** The LLM only interprets the brief. It does **not** calculate prices, perform final catalog matching, or resolve contradictions. Matching, quantities, pricing, and human resolution all run in deterministic Python code.

## Setup

### Prerequisites

- Python 3.10+
- OpenAI API key (for live PDF extraction)

### Install dependencies

```bash
pip install -r requirements.txt
```

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | For live extraction | OpenAI API key used by the official Python SDK |

Example (PowerShell):

```powershell
$env:OPENAI_API_KEY = "sk-your-key-here"
```

Optional: store the key in a local `.env` file (gitignored) and load it in your shell before running commands.

## Running the Streamlit review UI

```bash
streamlit run app/web.py
```

The UI supports:

- Uploading `data/client_brief.pdf` (or any brief PDF)
- Running extraction + quote generation
- Viewing all human-review flags
- **Resolving the LED screen contradiction** (6m×3m vs ≥8m wide) and recalculating the quote deterministically
- Viewing line-level cost breakdown (material, labour, equipment, margin, totals)

Use **“Use bundled sample brief (no OpenAI)”** in the sidebar to demo the UI without an API key.

## CLI (optional)

```bash
# Extract only
python -m app.cli extract data/client_brief.pdf -o output/extraction.json

# Full pipeline (requires OPENAI_API_KEY)
python -m app.cli quote data/client_brief.pdf -o output/quote.json

# Quote from saved extraction JSON (no OpenAI)
python -m app.cli quote-from-json output/extraction.json -o output/quote.json
```

## Tests

```bash
pytest tests/ -v
```

Tests use fixtures and mocks — no live OpenAI calls required.

## Project layout

| Path | Purpose |
|------|---------|
| `app/models.py` | Pydantic models for LLM extraction output |
| `app/extractor.py` | OpenAI brief extraction |
| `app/catalog.py` | Recipe catalog loader |
| `app/matcher.py` | Deterministic catalog matching |
| `app/quantities.py` | Deterministic quantity calculation |
| `app/pricing.py` | Deterministic pricing engine |
| `app/quote_generator.py` | Quote assembly |
| `app/resolution.py` | Human resolution (LED contradiction) |
| `app/review.py` | Review flag aggregation |
| `app/pipeline.py` | End-to-end orchestration |
| `app/web.py` | Streamlit human-review UI |
| `data/client_brief.pdf` | Sample client brief |
| `data/recipe_catalog.csv` | Recipe catalog |

## Human resolution (LED screen example)

The Nexus brief contains conflicting LED screen dimensions:

- Original email: **6m × 3m**
- Later update: **at least 8m wide**, height unspecified

The review UI lets a human choose:

1. Use original 6m × 3m (18 sqm)
2. Use 8m × 4m based on the original 2:1 ratio (32 sqm)
3. Exclude the LED line
4. Custom width × height

After resolution, the app recalculates quantity and price via `QuoteGenerator` — **no LLM call**.

## Assessment notes

- **Hologram box:** shown as CUSTOM / NOT IN CATALOG with SAR 14,000 procurement reference
- **Golden Falcon Welcome Arch:** adversarial footer instruction remains a critical review flag and never becomes a quote line
- **Cancelled breakout stage:** excluded from matching/pricing

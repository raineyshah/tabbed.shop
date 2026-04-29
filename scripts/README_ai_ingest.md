# AI-assisted product ingest

`scripts/ai_product_ingest.py` replaces the manual "Add Product" admin form
with an AI pipeline. Give it a product URL; it extracts everything the form
asks for (name, brand, category, subcategory, made in, price, made with,
made without, features, certifications, description, product image) and
inserts a row into `products`.

Before the AI does anything, the script loads every existing Brand,
Certification, Made With, Made Without, Feature/Attribute, and Made-In
country from your database and tells the model to **reuse the exact
existing spelling whenever a value refers to the same thing**. If the AI
encounters a genuinely new ingredient, material, feature, or certification,
it is added by default — brand-new certifications are created with no
image so you can fill in the logo later via admin "Edit Certification".

If you'd rather hold the line and reject anything new (e.g. to keep a
tightly curated catalog), pass the matching `--no-new-*` flag.

## 1. Paste your API key

Open `.env` in the project root and fill in:

```
ANTHROPIC_API_KEY=sk-ant-...your key here...
# Optional — override the default Claude model (must support tool use)
TABBED_AI_MODEL=claude-sonnet-4-5
```

The pipeline talks to the Anthropic Messages API and forces Claude to call
a single tool whose `input_schema` is our strict output contract, so we
always get fully-typed JSON back (no "parse the prose" games).

## 2. Install dependencies (first run only)

```
pip install -r requirements.txt
```

## 3. Use it

```bash
# 1) Dry run (no DB write). Shows you exactly what would be inserted.
python scripts/ai_product_ingest.py --url https://example.com/some-product --dry-run

# 2) Real run: inserts into products table using the same helpers the admin
#    form uses (brand lookup/create, certification lookup/create, image
#    normalization, etc.).
python scripts/ai_product_ingest.py --url https://example.com/some-product

# 3) Batch: one URL per line in urls.txt (comments with # allowed)
python scripts/ai_product_ingest.py --urls-file urls.txt

# 4) Strict mode: only reuse existing certifications / features.
#    Anything the AI can't map to an existing vocabulary entry is dropped.
python scripts/ai_product_ingest.py --url ... \
    --no-new-certifications \
    --no-new-features

# 5) Mark the products as verified / earning commission.
python scripts/ai_product_ingest.py --url ... --verified --earns-commission
```

## Flags

| Flag                          | Purpose                                                                 |
|-------------------------------|-------------------------------------------------------------------------|
| `--url URL`                   | Single product page URL.                                                |
| `--urls-file PATH`            | Batch file; one URL per line.                                           |
| `--dry-run`                   | Print what would be inserted; do not write to the DB.                   |
| `--model NAME`                | Override `TABBED_AI_MODEL` (default `claude-sonnet-4-5`).               |
| `--no-new-brand`              | Fail if the AI's brand isn't already in the DB.                         |
| `--no-new-certifications`     | Drop AI-proposed certifications that aren't already in `certifications`. Default: create them with a blank image. |
| `--no-new-features`           | Drop AI-proposed made_with / made_without / attributes values that aren't already in the catalog. Default: keep them. |
| `--earns-commission`          | Set `earns_commission=true` on the inserted row.                        |
| `--verified`                  | Set `is_verified=true` on the inserted row.                             |
| `--product-link URL`          | Override `product_link` (defaults to the URL being ingested).           |

## Certification badges

Many ecommerce pages render certification seals as bare `<img alt="OTCO">`
badges with no visible text. `BeautifulSoup.get_text()` doesn't include
`alt` attributes, so those badges would otherwise be invisible to the AI.
The scraper scans every page for:

1. Any `<img>` inside a container whose `class`/`id`/`aria-label`
   contains "cert", "badge", "trust", "seal" or "accreditation" (e.g.
   Dr. Bronner's `<div class="certifications-container">`).
2. Any `<img>` whose `alt` / `title` / `src` matches a certification
   keyword (`organic`, `vegan`, `non-gmo`, `fair-trade`, `leaping-bunny`,
   `ewg`, `usda`, `otco`, `roc`, `b-corp`, `fsc`, `oeko-tex`, …).

Each match is surfaced to Claude as a `{name, src}` entry in
`page_signals.certification_candidates`, and the prompt tells the model
it **must** emit one certification per distinct badge, keeping the
exact alt/title text as the name (so "OTCO" doesn't get merged into
"USDA Organic"). When a badge is a genuinely new certification, its
`src` is passed back as `image_url` so the badge art is imported along
with the row.

## What it matches against

The AI sees every existing catalog value before choosing:

- **Brands**: exact names from `brands` (displayed to AI; matched case-insensitively on the way back).
- **Certifications**: every row in `certifications`.
- **Made With / Made Without / Features**: distinct values from `products`.
- **Made In**: distinct values in `products.made_in` union'd with the default country list the admin form shows.
- **Main Category**: the canonical list (`Home`, `Garden`, `Kitchen`, `Home Improvement`, `Baggage`, `Clothing`, `Wellness`, `Food`, `Children`).
- **Subcategory**: must be one of the subcategories defined for the chosen main category, per `CANONICAL_SUBCATEGORIES_BY_MAIN` (and any additional ones already present in the `categories` table).

If the AI picks something new, the script matches case-insensitively
(also ignoring punctuation) before deciding it truly is new.

## Images

- **Product image**: pulled from the AI's chosen URL (validated to be one
  of the candidate image URLs on the page, with `og:image` as fallback).
  It's run through `_normalize_product_image_bytes` with
  `whiten_non_product=False`, which skips the flood-fill "whiten the
  background" step the admin form does for messy uploads. AI-downloaded
  brand shots are usually already clean, and the aggressive whitening
  step was eating into white-on-pink label text (e.g. Dr. Bronner's
  ingredient lists). The image is saved to `uploads/` + the
  `products.product_image` BLOB.
- **Brand image**: only fetched for *new* brands. If no logo URL is
  findable, the brand row is created without an image (exactly like the
  admin can do).
- **Certification image**: only fetched for *new* certifications when
  the AI proposes a `image_url`. If it doesn't, the certification row is
  created with a blank image and you can add one via admin "Edit
  Certification".

## What it does *not* do (yet)

- It does not upsert an existing product. Every run creates a new row;
  use admin "Edit Product" for updates.
- It does not try to classify a product that the AI cannot map to one
  of the canonical main categories — it raises an error instead.
- It does not yet run on a schedule. The current design is geared toward
  first-time adds. To automate later:
    1. Keep a list of product URLs somewhere (CSV, table, or feed).
    2. Invoke this script from cron / a scheduled task.
    3. Add an idempotency check (e.g. by product_link) upstream of
       `insert_product()` if you want refresh-in-place behavior.

## Safety / offline notes

- The script never talks to your FastAPI server — it writes directly to
  the same database as the app (set ``TABBED_DATABASE_URL`` for PostgreSQL, or
  the default local SQLite file) using the same SQLAlchemy models, so it runs
  whether or not `uvicorn` is up.
- If the AI response is malformed JSON, the script aborts that URL and
  continues with the rest of the batch (non-zero exit if any failed).
- `--dry-run` is the safest way to audit AI output before letting it
  touch the DB.

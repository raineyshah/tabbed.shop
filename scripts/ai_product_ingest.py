#!/usr/bin/env python3
"""AI-assisted product ingest for Tabbed.

Turns a product page URL (or a batch of URLs) into a new row in ``products``
the same way the admin "Add Product" form does, filling in:

    product_name, brand, main_category, subcategory, made_in, price,
    product_link, description, made_with, made_without, attributes (features),
    certifications, product image, is_verified, earns_commission

The AI is shown existing brands, certifications, made-in countries, and
vocabulary for Made With, Made Without, and Features. Made With / Made
Without / Features must only match values already in the vocabulary tables
(see Admin); unmatched strings are dropped unless ``--allow-new-features``
is passed. New certifications and brands can still be created subject to
their respective ``--no-new-*`` flags.

Typical usage
-------------

Paste your key into ``.env`` first (``OPENAI_API_KEY=sk-...``), then::

    # single URL, dry-run (prints what would be inserted, no DB writes)
    python scripts/ai_product_ingest.py --url https://brand.com/some-item --dry-run

    # single URL, actually insert
    python scripts/ai_product_ingest.py --url https://brand.com/some-item

    # batch (one URL per line in the file, # comments allowed)
    python scripts/ai_product_ingest.py --urls-file urls.txt

    # disallow new certifications (Made With / Without / Features already only match vocabulary)
    python scripts/ai_product_ingest.py --url ... --no-new-certifications

Exit code is non-zero if any URL fails. Successful rows print their new id.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, replace as dataclass_replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", override=True)
except ImportError:
    pass

import requests
from bs4 import BeautifulSoup

# Import ``app`` so shared routes/config load; use the same ``TABBED_DATABASE_URL`` / SQLite
# file as the web server when running this script.
import app as tabbed_app  # noqa: E402  (import after sys.path tweak)
from models import (  # noqa: E402
    SessionLocal,
    Brand,
    Category,
    Certification,
    Product,
)


# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_MODEL = (
    os.environ.get("TABBED_AI_MODEL", "claude-sonnet-4-5").strip()
    or "claude-sonnet-4-5"
)
FETCH_TIMEOUT = float(os.environ.get("TABBED_AI_FETCH_TIMEOUT") or "20")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 TabbedAIIngest/1.0"
)
PAGE_TEXT_CHAR_BUDGET = 12000  # chars of visible body text sent to the model

# Fallback list of countries the admin form uses when the catalog is empty.
FALLBACK_MADE_IN = [
    "Argentina", "Australia", "Austria", "Belgium", "Brazil", "Canada", "Chile",
    "China", "Colombia", "Czech Republic", "Denmark", "England", "Finland",
    "France", "Germany", "Greece", "Hungary", "India", "Indonesia", "Ireland",
    "Israel", "Italy", "Japan", "Malaysia", "Mexico", "Netherlands",
    "New Zealand", "Norway", "Peru", "Philippines", "Poland", "Portugal",
    "Romania", "Singapore", "South Africa", "South Korea", "Spain", "Sweden",
    "Switzerland", "Thailand", "Turkey", "United States", "Vietnam",
]


# ──────────────────────────────────────────────────────────────────────────────
# Data shapes
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class IngestOptions:
    dry_run: bool = False
    model: str = DEFAULT_MODEL
    allow_new_brand: bool = True
    # New certifications are created by default (blank image; fill in via admin
    # "Edit Certification" later). Pass --no-new-certifications to hold the line.
    allow_new_certifications: bool = True
    # made_with / made_without / features (attributes): only match existing
    # vocabulary; never mint new tags. Use --allow-new-features to override.
    allow_new_features: bool = False
    earns_commission: bool = False
    is_verified: bool = False
    override_product_link: Optional[str] = None
    # When True (admin "Populate with AI" only), never hard-fail on bad category /
    # missing made_in: use fallbacks + warnings so the form can be corrected.
    admin_form_friendly: bool = False


@dataclass
class Vocabularies:
    brands: List[Dict[str, Any]]          # {id, name, has_image}
    certifications: List[Dict[str, Any]]  # {id, name, has_image}
    made_with: List[str]
    made_without: List[str]
    attributes: List[str]
    made_in: List[str]
    main_categories: List[str]
    subcategories_by_main: Dict[str, List[str]]


@dataclass
class ResolvedProduct:
    product_name: str
    brand_name: str
    brand_is_new: bool
    brand_image_url: Optional[str]
    main_category: str
    subcategory: str
    made_in: str
    price: float
    product_link: Optional[str]
    description: Optional[str]
    made_with: List[str]
    made_without: List[str]
    attributes: List[str]
    certifications: List[Dict[str, Any]]  # {name, is_new, image_url}
    product_image_url: Optional[str]
    earns_commission: bool
    is_verified: bool
    # Size / pack variants. Empty list = single-SKU product (use top-level
    # price/name). Non-empty = one Product row is inserted per variant, with
    # ``size_label`` appended to product_name and ``price`` replacing the top-
    # level one. Each variant may also override ``product_image_url`` and
    # ``product_link`` when the page provides them.
    variants: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def summary_lines(self) -> List[str]:
        lines = [
            f"  name          : {self.product_name}",
            f"  brand         : {self.brand_name}"
                              + (" [NEW]" if self.brand_is_new else ""),
            f"  main/sub      : {self.main_category} / {self.subcategory or '—'}",
            f"  made in       : {self.made_in}",
        ]
        if self.variants:
            lines.append(
                f"  variants      : {len(self.variants)} size"
                + ("s" if len(self.variants) != 1 else "")
            )
            for v in self.variants:
                label = str(v.get("size_label") or "?")
                try:
                    price = float(v.get("price") or 0)
                except (TypeError, ValueError):
                    price = 0.0
                suffix_bits = []
                if v.get("product_image_url"):
                    suffix_bits.append("own image")
                if v.get("product_link"):
                    suffix_bits.append("deep link")
                suffix = f"  ({', '.join(suffix_bits)})" if suffix_bits else ""
                lines.append(f"    - {label:<14s}@ ${price:>7.2f}{suffix}")
        else:
            lines.append(f"  price         : {self.price:.2f}")
        lines.extend([
            f"  product_link  : {self.product_link or '—'}",
            f"  commission    : {self.earns_commission}",
            f"  verified      : {self.is_verified}",
            f"  made_with     : {', '.join(self.made_with) or '—'}",
            f"  made_without  : {', '.join(self.made_without) or '—'}",
            f"  features      : {', '.join(self.attributes) or '—'}",
        ])
        if self.certifications:
            for c in self.certifications:
                tag = " [NEW — add image in admin]" if c.get("is_new") else ""
                lines.append(f"  cert          : {c['name']}{tag}")
        else:
            lines.append("  cert          : —")
        lines.append(f"  product_image : {self.product_image_url or '—'}")
        if self.brand_image_url:
            lines.append(f"  brand_image   : {self.brand_image_url}")
        if self.warnings:
            lines.append("  warnings      :")
            for w in self.warnings:
                lines.append(f"    - {w}")
        return lines

    def explode_variants(self) -> List["ResolvedProduct"]:
        """Return one ResolvedProduct per variant.

        If ``variants`` is empty, returns ``[self]`` unchanged. Otherwise
        returns a list of copies with:
          * ``product_name`` = ``f"{base_name} — {size_label}"``
          * ``price`` = variant.price
          * ``product_image_url`` = variant override or base image
          * ``product_link`` = variant override or base link
          * ``variants`` = [] (children are standalone rows)
        """
        if not self.variants:
            return [self]
        out: List["ResolvedProduct"] = []
        for v in self.variants:
            label = str(v.get("size_label") or "").strip()
            try:
                price = float(v.get("price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            name = (
                f"{self.product_name} — {label}"
                if label and label.lower() not in self.product_name.lower()
                else (self.product_name if label else self.product_name)
            )
            out.append(
                dataclass_replace(
                    self,
                    product_name=name,
                    price=price,
                    product_image_url=(v.get("product_image_url") or self.product_image_url),
                    product_link=(v.get("product_link") or self.product_link),
                    variants=[],
                )
            )
        return out


# ──────────────────────────────────────────────────────────────────────────────
# Vocabulary loading
# ──────────────────────────────────────────────────────────────────────────────


def load_vocabularies(db) -> Vocabularies:
    brands = [
        {"id": b.id, "name": b.name, "has_image": bool(b.image)}
        for b in db.query(Brand).order_by(Brand.name.asc()).all()
    ]
    certifications = [
        {"id": c.id, "name": c.name, "has_image": bool(c.image)}
        for c in db.query(Certification).order_by(Certification.name.asc()).all()
    ]
    made_with, made_without, attributes = tabbed_app._distinct_attribute_tags(db)
    catalog_countries = sorted({
        (p.made_in or "").strip()
        for p in db.query(Product.made_in).all()
        if (p.made_in or "").strip()
    })
    made_in = sorted(set(catalog_countries) | set(FALLBACK_MADE_IN))
    main_categories = [name for _, name, _ in tabbed_app.CANONICAL_SHOP_CATEGORIES]
    subcategories_by_main: Dict[str, List[str]] = {}
    for main, subs in tabbed_app.CANONICAL_SUBCATEGORIES_BY_MAIN.items():
        db_subs = {
            row[0].strip()
            for row in db.query(Category.subcategory)
            .filter(Category.main_category == main, Category.parent_id.isnot(None))
            .all()
            if row and row[0] and row[0].strip()
        }
        merged = list(dict.fromkeys([*subs, *sorted(db_subs)]))
        subcategories_by_main[main] = merged
    return Vocabularies(
        brands=brands,
        certifications=certifications,
        made_with=made_with,
        made_without=made_without,
        attributes=attributes,
        made_in=made_in,
        main_categories=main_categories,
        subcategories_by_main=subcategories_by_main,
    )


# ──────────────────────────────────────────────────────────────────────────────
# URL fetching + HTML scraping
# ──────────────────────────────────────────────────────────────────────────────


def fetch_page(url: str) -> Tuple[str, str]:
    """Return (final_url, html_text). Raises on non-200/unreachable."""
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        timeout=FETCH_TIMEOUT,
        allow_redirects=True,
    )
    resp.raise_for_status()
    return resp.url, resp.text


def _first_meta(soup: BeautifulSoup, *queries: Tuple[str, str]) -> Optional[str]:
    for attr, value in queries:
        tag = soup.find("meta", attrs={attr: value})
        if tag and tag.get("content"):
            c = tag.get("content").strip()
            if c:
                return c
    return None


def extract_page_signals(url: str, html: str) -> Dict[str, Any]:
    """Pull out the structured bits we can from the page before handing to AI."""
    soup = BeautifulSoup(html, "html.parser")

    # ── script-tag signals (must run BEFORE we decompose <script> tags) ───────
    jsonld_products = _parse_jsonld_products(soup)
    shopify_product = _parse_shopify_product_json(soup)
    variants_hint = _derive_variants_hint(jsonld_products, shopify_product)

    for bad in soup(["script", "style", "noscript", "template"]):
        bad.decompose()

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    og_title = _first_meta(soup, ("property", "og:title"))
    og_description = _first_meta(soup, ("property", "og:description"),
                                  ("name", "description"))
    og_image = _first_meta(soup, ("property", "og:image:secure_url"),
                            ("property", "og:image"))
    og_site_name = _first_meta(soup, ("property", "og:site_name"))
    og_price_amount = _first_meta(
        soup,
        ("property", "og:price:amount"),
        ("property", "product:price:amount"),
        ("itemprop", "price"),
    )
    og_price_currency = _first_meta(
        soup,
        ("property", "og:price:currency"),
        ("property", "product:price:currency"),
        ("itemprop", "priceCurrency"),
    )

    h1 = soup.find("h1")
    h1_text = h1.get_text(" ", strip=True) if h1 else ""

    body_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    if len(body_text) > PAGE_TEXT_CHAR_BUDGET:
        body_text = body_text[:PAGE_TEXT_CHAR_BUDGET] + "…[truncated]"

    images: List[str] = []
    if og_image:
        images.append(og_image)
    for img in soup.find_all("img"):
        src = (img.get("src") or img.get("data-src") or "").strip()
        if not src or src.startswith("data:"):
            continue
        if src not in images:
            images.append(src)
        if len(images) >= 20:
            break

    certification_candidates = _extract_certification_candidates(soup)

    return {
        "final_url": url,
        "title": title,
        "h1": h1_text,
        "og_title": og_title,
        "og_description": og_description,
        "og_site_name": og_site_name,
        "og_image": og_image,
        "og_price_amount": og_price_amount,
        "og_price_currency": og_price_currency,
        "jsonld_products": jsonld_products,
        "candidate_images": images[:20],
        "certification_candidates": certification_candidates,
        "variants_hint": variants_hint,
        "body_text": body_text,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Script-tag parsing (JSON-LD + Shopify variant JSON)
# ──────────────────────────────────────────────────────────────────────────────


def _parse_jsonld_products(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ld in soup.find_all("script", attrs={"type": "application/ld+json"}):
        txt = ld.string or ld.get_text() or ""
        if not txt.strip():
            continue
        try:
            data = json.loads(txt)
        except (json.JSONDecodeError, ValueError):
            continue
        queue: List[Any] = data if isinstance(data, list) else [data]
        i = 0
        while i < len(queue):
            item = queue[i]
            i += 1
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                queue.extend(g for g in graph if isinstance(g, dict))
                continue
            t = item.get("@type")
            if isinstance(t, list):
                is_product = any("Product" in str(x) for x in t)
            else:
                is_product = "Product" in str(t or "")
            if is_product:
                out.append(item)
    return out


def _parse_shopify_product_json(soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
    """Find a Shopify-style product JSON blob embedded in the page.

    Shopify themes commonly expose the full product (including the variants
    array with per-size titles + prices) via either:
      * ``<script type="application/json" id="ProductJson-xyz">…</script>``
      * ``<script type="application/json" data-product-json>…</script>``

    Returns the parsed product dict (the one that has ``variants``) or None.
    """
    for script in soup.find_all("script", attrs={"type": "application/json"}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw or '"variants"' not in raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            if isinstance(data.get("variants"), list) and "title" in data:
                return data
            product = data.get("product")
            if isinstance(product, dict) and isinstance(product.get("variants"), list):
                return product
    return None


def _derive_variants_hint(
    jsonld_products: List[Dict[str, Any]],
    shopify_product: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Normalize variant data from JSON-LD / Shopify JSON into a flat list.

    Each entry is ``{label, price}`` (plus optional ``sku``, ``available``,
    ``url``, ``image_url``). If the page only offers a single SKU the list
    is empty — callers treat an empty list as "no variants".
    """
    out: List[Dict[str, Any]] = []
    seen: set = set()

    def _push(label: str, price: float, extras: Dict[str, Any]) -> None:
        label = (label or "").strip() or "Default"
        key = (label.lower(), round(price, 2))
        if key in seen:
            return
        seen.add(key)
        entry = {"label": label, "price": round(price, 2)}
        for k in ("sku", "available", "url", "image_url", "currency"):
            v = extras.get(k)
            if v not in (None, ""):
                entry[k] = v
        out.append(entry)

    if shopify_product and isinstance(shopify_product.get("variants"), list):
        for v in shopify_product["variants"]:
            if not isinstance(v, dict):
                continue
            label = (
                v.get("public_title")
                or v.get("title")
                or v.get("option1")
                or ""
            )
            raw = v.get("price")
            price = _coerce_shopify_price(raw)
            if price is None:
                continue
            extras: Dict[str, Any] = {}
            if v.get("sku"):
                extras["sku"] = str(v["sku"])
            if v.get("available") is not None:
                extras["available"] = bool(v["available"])
            img = v.get("featured_image")
            if isinstance(img, dict) and img.get("src"):
                extras["image_url"] = str(img["src"])
            elif isinstance(img, str):
                extras["image_url"] = img
            _push(label, price, extras)
        if out:
            return out

    for prod in jsonld_products:
        offers = prod.get("offers")
        items = offers if isinstance(offers, list) else ([offers] if isinstance(offers, dict) else [])
        if len(items) < 2:
            continue
        for o in items:
            if not isinstance(o, dict):
                continue
            try:
                price = float(o.get("price"))
            except (TypeError, ValueError):
                continue
            label = (o.get("name") or o.get("sku") or "").strip()
            extras: Dict[str, Any] = {}
            if o.get("sku"):
                extras["sku"] = str(o["sku"])
            if o.get("url"):
                extras["url"] = str(o["url"])
            if o.get("priceCurrency"):
                extras["currency"] = str(o["priceCurrency"])
            avail = str(o.get("availability") or "")
            if avail:
                extras["available"] = "InStock" in avail or "instock" in avail.lower()
            _push(label, price, extras)

    return out


def _coerce_shopify_price(raw: Any) -> Optional[float]:
    """Shopify serializes variant.price inconsistently: string ``"4.69"`` in
    some endpoints, integer cents ``469`` in others. Normalize to USD float."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return None
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    # If the value is an integer-like ≥ 100 with no fractional component, it's
    # almost certainly cents (Shopify ProductJson serializes 469 for "$4.69").
    if f >= 100 and f.is_integer():
        return round(f / 100.0, 2)
    return round(f, 2)


# Keywords that strongly suggest an <img> (or its ancestor container) is a
# certification / trust badge rather than a product photo.
_CERT_CONTAINER_HINT = re.compile(
    r"cert(ificat)?|badge|trust|seal|accreditation",
    re.IGNORECASE,
)
_CERT_KEYWORD_HINT = re.compile(
    r"certif|organic|vegan|non[- ]?gmo|gmo[- ]?free|fair[- ]?trade|fair[- ]?for[- ]?life|"
    r"leaping[- ]?bunny|cruelty[- ]?free|kosher|halal|usda|ewg|otco|oeko[- ]?tex|"
    r"fsc|b[- ]?corp|rainforest|regenerative|roc|fda|gluten[- ]?free|"
    r"carbon[- ]?neutral|climate[- ]?neutral|biodegradable|compostable",
    re.IGNORECASE,
)


def _extract_certification_candidates(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Return structured badges found on the page, each as {name, src, source}.

    We look for two patterns:
      1. Any ``<img>`` sitting inside a container whose id/class hints at
         certifications (e.g. ``class="certifications-container"``). These are
         the highest-signal badges — we keep them all, even ones whose alt
         text doesn't match a keyword.
      2. Any ``<img>`` whose own ``alt`` / ``title`` / ``src`` contains a
         certification-flavored keyword, regardless of where it sits.

    Results are deduped by normalized ``name`` (alt/title), preserving order.
    """
    out: List[Dict[str, str]] = []
    seen: set = set()

    def _push(img_tag: Any, source: str) -> None:
        alt = (img_tag.get("alt") or "").strip()
        title = (img_tag.get("title") or "").strip()
        src = (img_tag.get("src") or img_tag.get("data-src") or "").strip()
        name = alt or title
        if not name:
            return
        key = re.sub(r"\s+", " ", name).strip().lower()
        if key in seen:
            return
        seen.add(key)
        entry: Dict[str, str] = {"name": name, "source": source}
        if src:
            entry["src"] = src
        if title and title != name:
            entry["title"] = title
        out.append(entry)

    for container in soup.find_all(
        lambda tag: tag.name in ("div", "section", "ul", "aside", "figure")
        and _container_has_cert_hint(tag)
    ):
        for img in container.find_all("img"):
            _push(img, "container")

    # Only fall back to the keyword pass when we didn't find an explicit
    # certifications container on the page — otherwise the keyword scan can
    # poison the authoritative list with non-badge images (e.g. a main
    # product shot whose alt text happens to mention "organic").
    if not out:
        for img in soup.find_all("img"):
            alt = img.get("alt") or ""
            title = img.get("title") or ""
            src = img.get("src") or img.get("data-src") or ""
            blob = " ".join((alt, title, src))
            if _CERT_KEYWORD_HINT.search(blob):
                _push(img, "keyword")

    return out[:40]


def _container_has_cert_hint(tag: Any) -> bool:
    classes = " ".join(tag.get("class") or [])
    ident = tag.get("id") or ""
    aria = tag.get("aria-label") or ""
    return bool(_CERT_CONTAINER_HINT.search(" ".join((classes, ident, aria))))


# ──────────────────────────────────────────────────────────────────────────────
# AI call
# ──────────────────────────────────────────────────────────────────────────────


def _vocab_preview(names: Iterable[str], limit: int = 400) -> List[str]:
    out: List[str] = []
    for n in names:
        s = (n or "").strip()
        if s:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def build_prompt(url: str, signals: Dict[str, Any], vocab: Vocabularies) -> Tuple[str, str]:
    system = (
        "You are a catalog data-entry assistant for a small shopping site. "
        "Given a product page, extract the fields the admin would otherwise type "
        "by hand. You MUST prefer values that already exist in the site's "
        "vocabularies; only propose a new value when nothing in the provided "
        "list reasonably describes the product. Keep descriptions to 1–3 short "
        "sentences written for shoppers — no marketing fluff, no emojis. "
        f"You MUST call the '{_INGEST_TOOL_NAME}' tool exactly once with the "
        "extracted values; do not reply with plain text."
    )

    user_payload = {
        "url": url,
        "page_signals": signals,
        "existing_vocabularies": {
            "brands": [b["name"] for b in vocab.brands],
            "certifications": [c["name"] for c in vocab.certifications],
            "made_with": _vocab_preview(vocab.made_with),
            "made_without": _vocab_preview(vocab.made_without),
            "attributes": _vocab_preview(vocab.attributes),
            "made_in": vocab.made_in,
            "main_categories": vocab.main_categories,
            "subcategories_by_main": vocab.subcategories_by_main,
        },
        "rules": [
            "made_with, made_without, and attributes (features) are STRICTLY limited to the strings "
            "listed in existing_vocabularies for each field. You MUST only output items from those lists, "
            "using case-insensitive matching to the closest existing string when the page wording differs. "
            "If the page names an ingredient that has no match in the list, OMIT it — you must NOT invent, "
            "or output new text for these three arrays. "
            "For matching: different substances are different: 'Organic Palm Kernel Oil' is NOT 'Organic Palm Oil'. "
            "When a page ingredient matches a list item ignoring case, use the list's exact spelling (including footnote markers * if present in the list).",
            "Within the made_with / made_without / attributes you do output, be thorough for anything that *does* match the lists; do not omit minor matched ingredients or features.",
            "subcategory MUST be one of the subcategories listed under the chosen main_category, or the empty string.",
            "Certifications rules (follow exactly):\n"
            "  1. When page_signals.certification_candidates is non-empty, that list is AUTHORITATIVE. "
            "Your 'certifications' array MUST be a 1-to-1 mapping of it: exactly one entry per item, "
            "in the same order, and NOTHING ELSE. Do not add certifications mentioned in the product "
            "description, marketing copy, body text, or jsonld — if it isn't in the candidates list as "
            "a real badge on the page, it does not go in the output.\n"
            "  2. Only when page_signals.certification_candidates is empty (no badges found at all) may "
            "you fall back to extracting certifications from the body text.\n"
            "  3. Do NOT merge two differently-named badges into one. 'OTCO', 'USDA Organic', and "
            "'Regenerative Organic Certified' are three DISTINCT certifications and must remain "
            "separate entries if they each appear as a badge. Same for 'Non GMO' vs "
            "'Non-GMO Project Verified', 'Vegan' vs 'Certified Vegan', 'Leaping Bunny' vs 'Cruelty Free'.\n"
            "  4. For each certification, match against existing_vocabularies.certifications. A match "
            "exists ONLY when both names refer to the SAME certifying body/program, ignoring just case "
            "and trailing symbols like '®' / '™' / '(tm)'. When matched, set is_new=false and use the "
            "EXACT existing spelling (including any ® / ™ characters from the existing entry); omit "
            "image_url. Otherwise set is_new=true, use the badge's alt/title text verbatim as the name, "
            "and set image_url to that badge's src as an absolute URL.",
            "Never invent a product_image_url; only use one that appears in candidate_images or jsonld_products.",
            "If price is not clearly shown in USD on the page, return 0.",
            "Variants (sizes / pack sizes): if the page offers the same product in multiple sizes with distinct prices — check page_signals.variants_hint first, then the JSON-LD offers array, then the visible size dropdown / radio buttons in the body — populate the 'variants' array with one entry per size. Use short, shopper-friendly labels like '3.4 oz', '16 oz', '32 oz', '1 gallon', '2-pack' (strip 'Size:' / 'Select Size' prefixes and brand noise). When 'variants' is non-empty, top-level 'price' is ignored and one product row is inserted per variant. If the page only sells one size, leave 'variants' empty.",
        ],
    }

    user = (
        "Extract a Tabbed catalog row for the product below by calling the "
        f"'{_INGEST_TOOL_NAME}' tool. All fields from the tool schema must be "
        "provided.\n\n"
        + json.dumps(user_payload, indent=2, ensure_ascii=False)
    )
    return system, user


# The tool Claude is forced to call. Its input_schema is our strict output contract.
_INGEST_TOOL_NAME = "record_product_draft"

_INGEST_TOOL_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "product_name": {
            "type": "string",
            "description": "Short, clean product name. Do not include the brand unless it is part of the product name.",
        },
        "brand_name": {
            "type": "string",
            "description": "Manufacturer / brand. Prefer an exact existing brand name when one matches.",
        },
        "brand_is_new": {
            "type": "boolean",
            "description": "true only if no provided existing brand reasonably matches this product.",
        },
        "brand_image_url": {
            "type": ["string", "null"],
            "description": "Logo URL — provide only if brand_is_new is true and a logo URL is findable.",
        },
        "main_category": {
            "type": "string",
            "description": "Must be one of the provided main_categories.",
        },
        "subcategory": {
            "type": "string",
            "description": "Must be one of the subcategories listed under the chosen main_category, or empty string.",
        },
        "made_in": {
            "type": "string",
            "description": "Country of manufacture. Prefer the exact spelling from the provided made_in list.",
        },
        "price": {
            "type": "number",
            "description": "Price in USD; 0 if not clearly shown in USD on the page.",
        },
        "description": {
            "type": "string",
            "description": "1–3 short shopper-facing sentences. No marketing fluff, no emojis.",
        },
        "made_with": {
            "type": "array",
            "items": {"type": "string"},
            "description": "ONLY ingredients/materials that match existing_vocabularies.made_with (case-insensitive). Omit unknowns. Empty if none match.",
        },
        "made_without": {
            "type": "array",
            "items": {"type": "string"},
            "description": "ONLY exclusions that match existing_vocabularies.made_without. Omit unknowns. Empty if none match.",
        },
        "attributes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "ONLY feature tags that match existing_vocabularies.attributes. Omit unknowns. Empty if none match.",
        },
        "certifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "is_new": {
                        "type": "boolean",
                        "description": "true only when no existing certification name matches.",
                    },
                    "image_url": {
                        "type": ["string", "null"],
                        "description": "Badge image URL. Only set when is_new is true and a URL is findable.",
                    },
                },
                "required": ["name", "is_new"],
            },
        },
        "product_image_url": {
            "type": ["string", "null"],
            "description": "Best full-product image URL. Use only a URL that appears in candidate_images or jsonld_products.",
        },
        "variants": {
            "type": "array",
            "description": (
                "Size/price variants. Leave empty if the page sells only one size. "
                "When non-empty, one product row is inserted per variant; the top-level "
                "price is ignored and the top-level name is used as the base with "
                "'size_label' appended."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "size_label": {
                        "type": "string",
                        "description": "Short size/pack label, e.g. '3.4 oz', '16 oz', '32 oz', '1 gallon', '2-pack'.",
                    },
                    "price": {
                        "type": "number",
                        "description": "Price in USD for this size. 0 if unavailable or not shown.",
                    },
                    "product_image_url": {
                        "type": ["string", "null"],
                        "description": "Per-variant image URL, only if the page visibly swaps photos per size; otherwise null.",
                    },
                    "product_link": {
                        "type": ["string", "null"],
                        "description": "Per-variant URL (e.g. Shopify ?variant=... deep link), only if the page provides one; otherwise null.",
                    },
                },
                "required": ["size_label", "price"],
            },
        },
    },
    "required": [
        "product_name",
        "brand_name",
        "brand_is_new",
        "main_category",
        "subcategory",
        "made_in",
        "price",
        "description",
        "made_with",
        "made_without",
        "attributes",
        "certifications",
    ],
}


def call_ai(system_prompt: str, user_prompt: str, model: str) -> Dict[str, Any]:
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Paste your key into the .env file "
            "(ANTHROPIC_API_KEY=sk-ant-...) and try again."
        )
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "The 'anthropic' package is not installed. Run: pip install -r requirements.txt"
        ) from e

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=2048,
        temperature=0.1,
        system=system_prompt,
        tools=[
            {
                "name": _INGEST_TOOL_NAME,
                "description": (
                    "Record the extracted product draft for the Tabbed catalog. "
                    "You MUST call this tool exactly once with the fields below."
                ),
                "input_schema": _INGEST_TOOL_SCHEMA,
            }
        ],
        tool_choice={"type": "tool", "name": _INGEST_TOOL_NAME},
        messages=[{"role": "user", "content": user_prompt}],
    )

    for block in msg.content or []:
        block_type = getattr(block, "type", None)
        if block_type == "tool_use" and getattr(block, "name", "") == _INGEST_TOOL_NAME:
            payload = getattr(block, "input", None)
            if isinstance(payload, dict):
                return payload
            # anthropic-py may surface .input as a model — coerce via .model_dump()
            dump = getattr(payload, "model_dump", None)
            if callable(dump):
                return dump()
            if isinstance(payload, str):
                try:
                    return json.loads(payload)
                except json.JSONDecodeError as e:
                    raise RuntimeError(
                        f"AI tool input was not valid JSON: {e}\nRaw:\n{payload}"
                    ) from e
    raise RuntimeError(
        "Claude did not call the required tool. "
        f"Stop reason: {getattr(msg, 'stop_reason', '?')}"
    )


def _format_anthropic_client_error(exc: BaseException) -> Optional[str]:
    """If ``exc`` is from the Anthropic Python SDK, return a user-facing one-liner (else None)."""
    mod = getattr(type(exc), "__module__", "") or ""
    if not mod.startswith("anthropic"):
        return None
    primary = str(exc).strip()
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and (err.get("message") or err.get("type")):
            parts = [primary] if primary else []
            for key in ("message", "type"):
                if err.get(key):
                    bit = str(err[key]).strip()
                    if bit and bit not in primary:
                        parts.append(bit)
            return " — ".join(parts) if parts else "Anthropic API request failed."
    return primary or "Anthropic API request failed."


# ──────────────────────────────────────────────────────────────────────────────
# Normalization / resolution against existing vocabulary
# ──────────────────────────────────────────────────────────────────────────────


def _norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _match_ci(value: str, pool: Iterable[str]) -> Optional[str]:
    """Case-insensitive exact-then-fuzzy match; return the canonical spelling."""
    if not value:
        return None
    v = _norm(value)
    for p in pool:
        if _norm(p) == v:
            return p
    # loose match: ignore punctuation / parenthetical qualifiers
    v2 = re.sub(r"[^a-z0-9]+", " ", v).strip()
    for p in pool:
        p2 = re.sub(r"[^a-z0-9]+", " ", _norm(p)).strip()
        if p2 and p2 == v2:
            return p
    return None


def resolve_ai_payload(
    payload: Dict[str, Any],
    signals: Dict[str, Any],
    vocab: Vocabularies,
    options: IngestOptions,
) -> ResolvedProduct:
    warnings: List[str] = []

    product_name = (payload.get("product_name") or signals.get("og_title") or signals.get("title") or "").strip()
    if not product_name and options.admin_form_friendly:
        product_name = (signals.get("og_site_name") or "Product")[:120]
        if product_name and product_name != "Product":
            warnings.append("Product name was missing; using a page-derived placeholder — edit the name in the form.")
        else:
            warnings.append("Product name was missing; filled with placeholder 'Product' — edit in the form.")
    if not product_name:
        raise ValueError("AI produced no product_name and the page had no title.")

    # ── brand ────────────────────────────────────────────────────────────────
    brand_name_raw = (payload.get("brand_name") or "").strip()
    brand_is_new = bool(payload.get("brand_is_new"))
    brand_pool = [b["name"] for b in vocab.brands]
    matched = _match_ci(brand_name_raw, brand_pool)
    if matched:
        brand_name = matched
        brand_is_new = False
    else:
        brand_name = brand_name_raw
        brand_is_new = True
        if not options.allow_new_brand:
            raise ValueError(
                f"Brand '{brand_name_raw}' is not in the existing list and "
                "--allow-new-brand is disabled."
            )
    brand_image_url = payload.get("brand_image_url") if brand_is_new else None

    if not brand_name:
        raise ValueError("Brand name missing.")

    # ── category / subcategory ───────────────────────────────────────────────
    main_raw = (payload.get("main_category") or "").strip()
    main_category = _match_ci(main_raw, vocab.main_categories) if main_raw else None
    if not main_category and main_raw and options.admin_form_friendly:
        mrl = main_raw.lower()
        for cat in vocab.main_categories:
            cl = cat.lower()
            if mrl in cl or cl in mrl:
                main_category = cat
                warnings.append(
                    f"main_category: loosely matched {main_raw!r} → {main_category!r}."
                )
                break
    if not main_category and options.admin_form_friendly and vocab.main_categories:
        main_category = vocab.main_categories[0]
        warnings.append(
            f"main_category {main_raw!r} is not in the shop list; defaulted to {main_category!r}. "
            "Choose the right category in the form."
        )
    if not main_category:
        raise ValueError(
            f"main_category '{payload.get('main_category')}' is not one of "
            f"{vocab.main_categories}."
        )
    subcategory = (payload.get("subcategory") or "").strip()
    if subcategory:
        sub_match = _match_ci(subcategory, vocab.subcategories_by_main.get(main_category, []))
        if sub_match:
            subcategory = sub_match
        else:
            warnings.append(
                f"subcategory '{subcategory}' not recognized under {main_category}; leaving empty."
            )
            subcategory = ""

    # ── made_in ──────────────────────────────────────────────────────────────
    made_in_raw = (payload.get("made_in") or "").strip()
    matched_country = _match_ci(made_in_raw, vocab.made_in)
    if matched_country:
        made_in = matched_country
    elif made_in_raw:
        made_in = made_in_raw
        warnings.append(f"Made-in country '{made_in_raw}' not in the known list; stored as-is.")
    else:
        if options.admin_form_friendly and vocab.made_in:
            made_in = ""
            for preferred in ("United States", "USA", "U.S.A.", "US", "U.S."):
                m = _match_ci(preferred, vocab.made_in)
                if m:
                    made_in = m
                    break
            if not made_in:
                made_in = vocab.made_in[0]
            warnings.append(
                f"Could not determine country of manufacture; defaulted to {made_in!r}. "
                "Set Made In in the form if that is wrong."
            )
        else:
            raise ValueError("AI could not determine made_in; the admin form requires it.")

    # ── price ────────────────────────────────────────────────────────────────
    try:
        price = float(payload.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    if price < 0:
        price = 0.0
    if price == 0 and signals.get("og_price_amount"):
        try:
            price = float(str(signals["og_price_amount"]).replace(",", ""))
        except ValueError:
            pass

    # ── bulk list fields ─────────────────────────────────────────────────────
    def _filter_vocab_list(label: str, items: Any, pool: List[str], allow_new: bool) -> List[str]:
        raw = items if isinstance(items, list) else []
        keep: List[str] = []
        seen: set = set()
        for item in raw:
            s = (str(item) if not isinstance(item, dict) else str(item.get("name") or "")).strip()
            if not s:
                continue
            canon = _match_ci(s, pool)
            if canon:
                val = canon
            elif allow_new:
                val = s
            else:
                warnings.append(
                    f"Dropped {label} '{s}' (not in existing vocabulary; add in Admin or use --allow-new-features)."
                )
                continue
            if val.lower() not in seen:
                seen.add(val.lower())
                keep.append(val)
        return keep

    made_with = _filter_vocab_list("made_with", payload.get("made_with"), vocab.made_with, options.allow_new_features)
    made_without = _filter_vocab_list("made_without", payload.get("made_without"), vocab.made_without, options.allow_new_features)
    attributes = _filter_vocab_list("attribute", payload.get("attributes"), vocab.attributes, options.allow_new_features)

    # ── certifications ───────────────────────────────────────────────────────
    certs_out: List[Dict[str, Any]] = []
    cert_pool = [c["name"] for c in vocab.certifications]
    for raw in payload.get("certifications") or []:
        if isinstance(raw, str):
            raw = {"name": raw}
        if not isinstance(raw, dict):
            continue
        name = (raw.get("name") or "").strip()
        if not name:
            continue
        canon = _match_ci(name, cert_pool)
        if canon:
            certs_out.append({"name": canon, "is_new": False, "image_url": None})
            continue
        if not options.allow_new_certifications:
            warnings.append(
                f"Dropped new certification '{name}' (no existing match; "
                "remove --no-new-certifications to keep)."
            )
            continue
        image_url = (raw.get("image_url") or "").strip() or None
        certs_out.append({"name": name, "is_new": True, "image_url": image_url})

    # ── images ───────────────────────────────────────────────────────────────
    candidate_images = signals.get("candidate_images") or []
    product_image_url = (payload.get("product_image_url") or "").strip() or None
    if product_image_url and product_image_url not in candidate_images:
        if not product_image_url.startswith("http"):
            warnings.append(f"Ignored non-http product_image_url: {product_image_url}")
            product_image_url = None
    if not product_image_url:
        if signals.get("og_image"):
            product_image_url = signals["og_image"]
        elif candidate_images:
            product_image_url = candidate_images[0]

    # ── scalars ──────────────────────────────────────────────────────────────
    product_link = options.override_product_link or signals.get("final_url")
    description = (payload.get("description") or "").strip() or None

    # ── variants ─────────────────────────────────────────────────────────────
    variants_out: List[Dict[str, Any]] = []
    raw_variants = payload.get("variants") or []
    seen_variant_keys: set = set()
    if isinstance(raw_variants, list):
        for v in raw_variants:
            if not isinstance(v, dict):
                continue
            label = (v.get("size_label") or "").strip()
            if not label:
                continue
            try:
                vprice = float(v.get("price") or 0)
            except (TypeError, ValueError):
                vprice = 0.0
            if vprice < 0:
                vprice = 0.0
            key = (label.lower(), round(vprice, 2))
            if key in seen_variant_keys:
                continue
            seen_variant_keys.add(key)
            entry: Dict[str, Any] = {"size_label": label, "price": round(vprice, 2)}
            img = (v.get("product_image_url") or "").strip()
            if img and img.startswith("http"):
                entry["product_image_url"] = img
            link = (v.get("product_link") or "").strip()
            if link and link.startswith("http"):
                entry["product_link"] = link
            variants_out.append(entry)
    if len(variants_out) == 1:
        # One "variant" is effectively no variant — fold it back into the base
        # product to avoid creating a single row with an awkward size suffix.
        only = variants_out[0]
        if price == 0 and only.get("price"):
            price = only["price"]
        if not product_image_url and only.get("product_image_url"):
            product_image_url = only["product_image_url"]
        if only.get("product_link"):
            product_link = only["product_link"]
        variants_out = []

    return ResolvedProduct(
        product_name=product_name,
        brand_name=brand_name,
        brand_is_new=brand_is_new,
        brand_image_url=brand_image_url,
        main_category=main_category,
        subcategory=subcategory,
        made_in=made_in,
        price=price,
        product_link=product_link,
        description=description,
        made_with=made_with,
        made_without=made_without,
        attributes=attributes,
        certifications=certs_out,
        product_image_url=product_image_url,
        earns_commission=options.earns_commission,
        is_verified=options.is_verified,
        variants=variants_out,
        warnings=warnings,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Image fetch helpers
# ──────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=64)
def _download(url: str) -> Optional[bytes]:
    """Fetch bytes from a URL, with a small in-process cache so shared images
    (e.g. the same product shot used by every size variant) are fetched once."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=FETCH_TIMEOUT,
            allow_redirects=True,
        )
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as e:
        print(f"  ! image download failed for {url}: {e}", file=sys.stderr)
        return None


def _safe_fragment(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")
    return s or "item"


# ──────────────────────────────────────────────────────────────────────────────
# DB write
# ──────────────────────────────────────────────────────────────────────────────


def _resolve_or_create_brand(db, resolved: ResolvedProduct) -> Brand:
    existing = db.query(Brand).filter(Brand.name == resolved.brand_name).first()
    if existing:
        return existing
    blob: Optional[bytes] = None
    if resolved.brand_image_url:
        raw = _download(resolved.brand_image_url)
        if raw:
            try:
                blob = tabbed_app._normalize_brand_image_bytes(raw)
            except Exception as e:
                print(f"  ! brand logo normalization failed: {e}", file=sys.stderr)
                blob = None
    row = Brand(name=resolved.brand_name, link="", image=blob)
    db.add(row)
    db.flush()
    return row


def _resolve_or_create_certifications(db, resolved: ResolvedProduct) -> List[Certification]:
    out: List[Certification] = []
    for c in resolved.certifications:
        name = c["name"]
        row = db.query(Certification).filter(Certification.name == name).first()
        if row is None:
            blob: Optional[bytes] = None
            if c.get("image_url"):
                raw = _download(c["image_url"])
                if raw:
                    try:
                        blob = tabbed_app._normalize_brand_image_bytes(raw)
                    except Exception as e:
                        print(f"  ! cert image normalization failed ({name}): {e}", file=sys.stderr)
                        blob = None
            row = Certification(name=name, link="", image=blob)
            db.add(row)
            db.flush()
        out.append(row)
    return out


def insert_product(resolved: ResolvedProduct) -> int:
    db = SessionLocal()
    try:
        brand_row = _resolve_or_create_brand(db, resolved)
        certs = _resolve_or_create_certifications(db, resolved)
        made_with_n, made_without_n, attributes_n = tabbed_app._normalize_product_tag_lists_to_vocab(
            db, resolved.made_with, resolved.made_without, resolved.attributes
        )

        image_blob: Optional[bytes] = None
        image_filename: Optional[str] = None
        if resolved.product_image_url:
            raw = _download(resolved.product_image_url)
            if raw:
                try:
                    # AI-downloaded product shots are already ecommerce-clean.
                    # Skip the flood-fill whitening step so white-on-pink label
                    # text (e.g. Dr. Bronner's ingredient list) survives.
                    image_blob = tabbed_app._normalize_product_image_bytes(
                        raw, whiten_non_product=False
                    )
                except Exception as e:
                    print(f"  ! product image normalization failed: {e}", file=sys.stderr)
                    image_blob = None
                if image_blob:
                    image_filename = (
                        f"product_{_safe_fragment(resolved.product_name)}_ai.jpg"
                    )
                    try:
                        uploads_dir = BASE_DIR / "uploads"
                        uploads_dir.mkdir(exist_ok=True)
                        (uploads_dir / image_filename).write_bytes(image_blob)
                    except OSError as e:
                        print(f"  ! could not persist image to uploads/: {e}", file=sys.stderr)

        product = Product(
            product_name=resolved.product_name,
            brand_id=brand_row.id,
            main_category=resolved.main_category,
            subcategory=resolved.subcategory or "",
            made_in=resolved.made_in,
            price=resolved.price,
            product_link=resolved.product_link,
            earns_commission=resolved.earns_commission,
            made_with=made_with_n,
            made_without=made_without_n,
            attributes=attributes_n,
            description=resolved.description,
            product_image=image_blob,
            product_image_filename=image_filename,
            is_verified=resolved.is_verified,
        )
        product.certifications = certs
        db.add(product)
        db.commit()
        db.refresh(product)
        return product.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────────────


def ingest_one(url: str, options: IngestOptions) -> Optional[int]:
    print(f"\n── {url}")
    final_url, html = fetch_page(url)
    signals = extract_page_signals(final_url, html)

    db = SessionLocal()
    try:
        vocab = load_vocabularies(db)
    finally:
        db.close()

    system, user = build_prompt(final_url, signals, vocab)
    print(f"  · calling {options.model}…")
    t0 = time.time()
    payload = call_ai(system, user, options.model)
    print(f"  · AI responded in {time.time() - t0:.1f}s")

    resolved = resolve_ai_payload(payload, signals, vocab, options)
    for line in resolved.summary_lines():
        print(line)

    if options.dry_run:
        print("  · --dry-run set; skipping DB write.")
        return None

    rows = resolved.explode_variants()
    if len(rows) == 1:
        pid = insert_product(rows[0])
        print(f"  ✓ inserted product id={pid}")
        return pid

    first_pid: Optional[int] = None
    for row, variant in zip(rows, resolved.variants):
        pid = insert_product(row)
        label = variant.get("size_label") or row.product_name
        print(f"  ✓ inserted product id={pid}  ({label} @ ${row.price:.2f})")
        if first_pid is None:
            first_pid = pid
    return first_pid


def resolved_product_to_form_dict(r: ResolvedProduct) -> Dict[str, Any]:
    """Shape returned by the admin "Populate with AI" API for the add-product form."""
    cert_list: List[Dict[str, str]] = []
    for c in r.certifications or []:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        if name:
            cert_list.append({"name": name})
    return {
        "name": r.product_name,
        "product_name": r.product_name,
        "product_link": (r.product_link or "").strip() or None,
        "price": r.price,
        "description": (r.description or "") or "",
        "earns_commission": r.earns_commission,
        "is_verified": r.is_verified,
        "main_category": r.main_category,
        "subcategory": (r.subcategory or "") or "",
        "category": r.main_category,
        "brand_name": r.brand_name,
        "made_in": r.made_in,
        "made_with": list(r.made_with or []),
        "made_without": list(r.made_without or []),
        "attributes": list(r.attributes or []),
        "certifications": cert_list,
    }


def run_ingest_for_form(url: str) -> Dict[str, Any]:
    """
    Run fetch + AI + resolve; does not insert a product. Used by the admin UI.

    Returns ``{"product": {…}, "messages": [str, …]}``. ``product`` keys match
    :func:`apcPopulateFromProduct` in ``admin-product-create.js``.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("URL is required.")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("URL must start with http:// or https://")

    try:
        final_url, html = fetch_page(url)
    except Exception as e:
        raise ValueError(
            f"Could not download this product page: {e}. "
            "Check the URL, your network, and that the site allows automated requests."
        ) from e
    signals = extract_page_signals(final_url, html)
    db = SessionLocal()
    try:
        vocab = load_vocabularies(db)
    finally:
        db.close()
    system, user = build_prompt(final_url, signals, vocab)
    model = (os.environ.get("TABBED_AI_MODEL", "") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    try:
        payload = call_ai(system, user, model)
    except Exception as e:
        hint = _format_anthropic_client_error(e)
        if hint:
            raise ValueError(hint) from e
        raise
    options = IngestOptions(
        dry_run=True,
        model=model,
        allow_new_brand=True,
        allow_new_certifications=True,
        allow_new_features=False,
        earns_commission=False,
        is_verified=False,
        override_product_link=None,
        admin_form_friendly=True,
    )
    resolved = resolve_ai_payload(payload, signals, vocab, options)
    rows = resolved.explode_variants()
    if not rows:
        raise ValueError("AI produced no product row for this page.")
    r0 = rows[0]
    messages: List[str] = []
    if len(rows) > 1:
        messages.append(
            f"This page has {len(rows)} size variants; the form is filled with the first "
            f"({r0.product_name!r})."
        )
    for w in (r0.warnings or []):
        if w:
            messages.append(str(w))
    return {
        "product": resolved_product_to_form_dict(r0),
        "messages": messages,
    }


def read_urls_file(path: Path) -> List[str]:
    urls: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        urls.append(s)
    return urls


def parse_args(argv: Optional[List[str]] = None) -> Tuple[List[str], IngestOptions]:
    p = argparse.ArgumentParser(description="AI-assisted product ingest for Tabbed.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="Single product page URL to ingest.")
    src.add_argument("--urls-file", type=Path, help="File with one URL per line.")

    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be inserted; do not write to DB.")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"Claude model (default: {DEFAULT_MODEL}).")
    p.add_argument("--no-new-brand", action="store_true",
                   help="Fail if the AI's brand is not already in the DB.")
    p.add_argument("--no-new-certifications", action="store_true",
                   help="Drop AI-proposed certifications that don't already exist "
                        "(default: create them with a blank image).")
    p.add_argument("--allow-new-features", action="store_true",
                   help="Allow AI to add NEW made_with / made_without / features strings "
                        "not already in the DB (default: only match existing vocabulary).")
    p.add_argument("--earns-commission", action="store_true",
                   help="Mark these products as earning commission (affiliate).")
    p.add_argument("--verified", action="store_true",
                   help="Mark these products as verified.")
    p.add_argument("--product-link",
                   help="Override product_link (default: the URL being ingested).")

    args = p.parse_args(argv)

    if args.url:
        urls = [args.url]
    else:
        if not args.urls_file.exists():
            p.error(f"--urls-file not found: {args.urls_file}")
        urls = read_urls_file(args.urls_file)
        if not urls:
            p.error("--urls-file had no URLs.")

    options = IngestOptions(
        dry_run=args.dry_run,
        model=args.model,
        allow_new_brand=not args.no_new_brand,
        allow_new_certifications=not args.no_new_certifications,
        allow_new_features=args.allow_new_features,
        earns_commission=args.earns_commission,
        is_verified=args.verified,
        override_product_link=args.product_link,
    )
    return urls, options


def main(argv: Optional[List[str]] = None) -> int:
    urls, options = parse_args(argv)

    failures = 0
    for url in urls:
        try:
            ingest_one(url, options)
        except Exception as e:
            failures += 1
            print(f"  ✗ failed: {e}", file=sys.stderr)

    if failures:
        print(f"\n{failures}/{len(urls)} URL(s) failed.", file=sys.stderr)
        return 1
    print(f"\nDone. {len(urls)} URL(s) processed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

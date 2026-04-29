#!/usr/bin/env python3
"""Copy all ORM tables from a SQLite file into PostgreSQL (empty or wiped).

Requires:
  - Target: ``TABBED_DATABASE_URL`` as ``postgresql+psycopg2://...`` (set in env or .env).
  - Source: ``--sqlite`` path to ``tabbed.db`` (or set ``TABBED_SQLITE_PATH``).

Example:
  export TABBED_DATABASE_URL='postgresql+psycopg2://tabbed:secret@YOUR_VM_IP:5432/tabbed'
  python scripts/sqlite_to_postgres.py --sqlite /path/to/tabbed.db

  # Add --wipe to TRUNCATE app tables on Postgres first (destructive).
  python scripts/sqlite_to_postgres.py --sqlite tabbed.db --wipe
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(_BASE / ".env", override=False)
    except ImportError:
        pass


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Merge SQLite tabbed.db into PostgreSQL.")
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=None,
        help="Path to SQLite file (default: TABBED_SQLITE_PATH or project tabbed.db / products.db)",
    )
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="TRUNCATE all app tables on PostgreSQL before copy (destructive).",
    )
    args = parser.parse_args()

    dest_url = (os.environ.get("TABBED_DATABASE_URL") or "").strip()
    if not dest_url.startswith("postgresql"):
        print(
            "Set TABBED_DATABASE_URL to a PostgreSQL URL, e.g.\n"
            "  postgresql+psycopg2://USER:PASS@HOST:5432/DBNAME?sslmode=require",
            file=sys.stderr,
        )
        return 1

    sqlite_path = args.sqlite
    if sqlite_path is None:
        env_path = (os.environ.get("TABBED_SQLITE_PATH") or "").strip()
        if env_path:
            sqlite_path = Path(env_path).expanduser()
        else:
            tabbed = _BASE / "tabbed.db"
            legacy = _BASE / "products.db"
            if tabbed.exists():
                sqlite_path = tabbed
            elif legacy.exists():
                sqlite_path = legacy
            else:
                print("No SQLite file found; pass --sqlite PATH", file=sys.stderr)
                return 1

    sqlite_path = sqlite_path.expanduser().resolve()
    if not sqlite_path.is_file():
        print(f"SQLite file not found: {sqlite_path}", file=sys.stderr)
        return 1

    from sqlalchemy import create_engine, insert, select, text
    from sqlalchemy.orm import Session, sessionmaker

    from models import (
        Base,
        Brand,
        Category,
        Certification,
        Product,
        User,
        UserFavorite,
        VocabFeature,
        VocabMadeWith,
        VocabMadeWithout,
        product_certifications,
    )

    sqlite_url = f"sqlite:///{sqlite_path}"
    pg_engine = create_engine(dest_url, pool_pre_ping=True)
    sq_engine = create_engine(
        sqlite_url,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    Base.metadata.create_all(bind=pg_engine)

    if args.wipe:
        with pg_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    TRUNCATE TABLE
                        user_favorites,
                        product_certifications,
                        products,
                        users,
                        categories,
                        brands,
                        certifications,
                        vocab_features,
                        vocab_made_without,
                        vocab_made_with
                    RESTART IDENTITY CASCADE;
                    """
                )
            )

    def merge_all(sq: Session, pg: Session, rows: list) -> None:
        for r in rows:
            sq.expunge(r)
            pg.merge(r)

    sq = Session(sq_engine)
    pg = Session(pg_engine)

    try:
        categories = list(sq.scalars(select(Category).order_by(Category.id)).all())
        inserted_cat: set[int] = set()
        remaining = {c.id: c for c in categories}
        while remaining:
            progressed = False
            for cid, c in list(remaining.items()):
                pid = c.parent_id
                if pid is None or pid in inserted_cat:
                    sq.expunge(c)
                    pg.merge(c)
                    inserted_cat.add(cid)
                    del remaining[cid]
                    progressed = True
            if not progressed:
                raise RuntimeError("Category hierarchy cycle or missing parent in SQLite data")
        pg.flush()

        for model in (Brand, Certification, VocabMadeWith, VocabMadeWithout, VocabFeature):
            rows = list(sq.scalars(select(model)).all())
            merge_all(sq, pg, rows)
            pg.flush()

        products = list(sq.scalars(select(Product).order_by(Product.id)).all())
        merge_all(sq, pg, products)
        pg.flush()

        tab = sq.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='product_certifications'"
            )
        ).first()
        if tab:
            links = sq.execute(
                text("SELECT product_id, certification_id FROM product_certifications")
            ).all()
            for row in links:
                product_id, certification_id = row[0], row[1]
                pg.execute(
                    insert(product_certifications).values(
                        product_id=product_id, certification_id=certification_id
                    )
                )
            pg.flush()

        users = list(sq.scalars(select(User)).all())
        merge_all(sq, pg, users)
        pg.flush()

        favorites = list(sq.scalars(select(UserFavorite).order_by(UserFavorite.id)).all())
        merge_all(sq, pg, favorites)

        pg.commit()
    except Exception:
        pg.rollback()
        raise
    finally:
        sq.close()
        pg.close()

    # Align PostgreSQL sequences with copied IDs (SERIAL / identity on autoincrement tables).
    with pg_engine.begin() as conn:
        for table in (
            "brands",
            "certifications",
            "vocab_made_with",
            "vocab_made_without",
            "vocab_features",
        ):
            mx = conn.execute(text(f"SELECT MAX(id) FROM {table}")).scalar()
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table}
            ).scalar()
            if not seq:
                continue
            if mx is None:
                conn.execute(text(f"SELECT setval(:seq, 1, false)"), {"seq": seq})
            else:
                conn.execute(text(f"SELECT setval(:seq, :mx, true)"), {"seq": seq, "mx": mx})

    print(f"Done: copied {sqlite_path} -> PostgreSQL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

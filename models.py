from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    Text,
    LargeBinary,
    Boolean,
    DateTime,
    JSON,
    ForeignKey,
    UniqueConstraint,
    Table,
)
from datetime import datetime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, synonym
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

DATABASE_URL = (os.environ.get("TABBED_DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("TABBED_DATABASE_URL is not set. Configure it in your .env file.")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=int(os.environ.get("TABBED_DB_POOL_SIZE") or "5"),
    max_overflow=int(os.environ.get("TABBED_DB_MAX_OVERFLOW") or "10"),
)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Category(Base):
    """Top-level shelf rows (parent_id NULL) or subcategory rows tied to one main category (1:many).

    Column names align with ``products.main_category`` and ``products.subcategory``:
    ``main_category`` is the shelf name (same as parent.name for child rows); ``subcategory`` is set
    only on child rows and matches product subcategory strings.
    """

    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    parent_id = Column(Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=True, index=True)
    subcategory = Column(String, nullable=True)
    main_category = Column(String, nullable=False)

    parent = relationship(
        "Category",
        remote_side=[id],
        back_populates="children",
    )
    children = relationship(
        "Category",
        back_populates="parent",
        foreign_keys=[parent_id],
    )


class User(Base):
    __tablename__ = "users"

    email = Column(String(320), primary_key=True, index=True)
    username = Column(String(64), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # False until first-time user picks a public username after email verification.
    username_confirmed = Column(Boolean, nullable=False, default=True)
    avatar_image = Column(LargeBinary, nullable=True)
    avatar_mime_type = Column(String(64), nullable=True)
    # Set only when the user saves via POST /api/me/avatar (hides legacy/generated blobs).
    avatar_uploaded_at = Column(DateTime, nullable=True)
    # Merged with server defaults for GET/POST /api/settings (e.g. favorites-visible).
    profile_settings = Column(JSON, nullable=True)


product_certifications = Table(
    "product_certifications",
    Base.metadata,
    Column("product_id", Integer, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "certification_id",
        Integer,
        ForeignKey("certifications.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Certification(Base):
    __tablename__ = "certifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    link = Column(String(2048), nullable=False, default="")
    image = Column(LargeBinary, nullable=True)
    products = relationship(
        "Product",
        secondary=product_certifications,
        back_populates="certifications",
    )


class Brand(Base):
    __tablename__ = "brands"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    link = Column(String(2048), nullable=False, default="")
    image = Column(LargeBinary, nullable=True)
    products = relationship("Product", back_populates="brand")


class VocabMadeWith(Base):
    """Canonical ingredient / material labels for the Made With column (admin + AI)."""

    __tablename__ = "vocab_made_with"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(512), nullable=False)


class VocabMadeWithout(Base):
    """Canonical free-of labels for the Made Without column."""

    __tablename__ = "vocab_made_without"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(512), nullable=False)


class VocabFeature(Base):
    """Canonical feature / attribute tag labels (``products.attributes`` — Features in UI)."""

    __tablename__ = "vocab_features"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(512), nullable=False)


class Product(Base):
    """Catalog rows; API maps product_name → name and JSON list fields."""

    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    product_name = Column(String, nullable=False)
    brand_id = Column(Integer, ForeignKey("brands.id", ondelete="RESTRICT"), nullable=False, index=True)
    main_category = Column(String, nullable=False)
    subcategory = Column(String, nullable=False, default="")
    # Legacy ORM name: DB column is main_category (products.category was migrated away).
    category = synonym("main_category")
    made_in = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    product_link = Column(String, nullable=True)
    earns_commission = Column(Boolean, default=False, nullable=False)
    product_image = Column(LargeBinary, nullable=True)
    product_image_filename = Column(String, nullable=True)
    made_with = Column(JSON, nullable=True)
    made_without = Column(JSON, nullable=True)
    attributes = Column(JSON, nullable=True)
    description = Column(Text, nullable=True)
    is_verified = Column(Boolean, default=False, nullable=False)

    brand = relationship("Brand", back_populates="products")
    certifications = relationship(
        "Certification",
        secondary=product_certifications,
        back_populates="products",
        order_by=Certification.id,
    )


class UserFavorite(Base):
    __tablename__ = "user_favorites"

    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(
        String(320),
        ForeignKey("users.email", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("user_email", "product_id", name="uq_user_favorite_product"),)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

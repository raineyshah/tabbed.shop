from fastapi import FastAPI, File, UploadFile, Form, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv

    # override=True: a blank TABBED_SMTP_PASSWORD in the shell/IDE would otherwise win over .env
    load_dotenv(_BASE_DIR / ".env", override=True)
except ImportError:
    pass

from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import text, func, update, or_, select
from sqlalchemy.exc import IntegrityError, OperationalError
from PIL import Image, ImageOps, UnidentifiedImageError
import numpy as np
from collections import deque
from contextlib import asynccontextmanager
import json
import shutil
import subprocess
import os
import re
import secrets
import base64
import hashlib
import hmac
import string
import threading
import time
import smtplib
import ssl
import socket
import logging
import io
import numbers
from email.message import EmailMessage
from urllib.parse import quote, unquote
from typing import Optional, List, Any, Union, Tuple, Set, Dict
from datetime import datetime

from models import (
    engine,
    Base,
    get_db,
    SessionLocal,
    Category,
    Product,
    User,
    UserFavorite,
    Brand,
    Certification,
    VocabMadeWith,
    VocabMadeWithout,
    VocabFeature,
    product_certifications,
)

# Set by auth / profile flow (HttpOnly recommended). Optional server default for local/dev.
CONTRIBUTOR_USERNAME_COOKIE = "tabbed_contributor_username"
# HttpOnly cookie: verified email pending first-time username choice (paired with no contributor cookie).
USERNAME_SETUP_COOKIE = "tabbed_username_setup"
_USERNAME_SETUP_TTL_SEC = 3600

# One-word paths that must not be used as public usernames (shop routes and static sections).
_RESERVED_PUBLIC_USERNAMES: Set[str] = {
    "admin",
    "api",
    "articles",
    "about",
    "search",
    "all",
    "user",
    "users",
    "login",
    "logout",
    "landing",
    "contribute",
    "partners",
    "contact",
    "privacy",
    "privacy-policy",
    "faq",
    "terms",
    "sign-in",
    "static",
    "affiliate-disclosure",
    "partner-with-us",
    "contributor-policy",
}

# Canonical shop categories: admin and validation use these slugs/labels. Data lives in the DB you provision.
# Subcategories match ``PRD.md`` (Main categories and subcategories).
CANONICAL_SHOP_CATEGORIES: Tuple[Tuple[str, str, int], ...] = (
    ("home", "Home", 10),
    ("garden", "Garden", 20),
    ("kitchen", "Kitchen", 30),
    ("home-improvement", "Home Improvement", 40),
    ("baggage", "Baggage", 50),
    ("clothing", "Clothing", 60),
    ("wellness", "Wellness", 70),
    ("food", "Food", 80),
    ("children", "Children", 90),
)

_CANONICAL_MAIN_CATEGORY_NAMES: Set[str] = {row[1] for row in CANONICAL_SHOP_CATEGORIES}
_CANONICAL_CATEGORY_SLUGS: Tuple[str, ...] = tuple(row[0] for row in CANONICAL_SHOP_CATEGORIES)

# Subcategory labels per main category name (matches ``products.subcategory`` strings).
CANONICAL_SUBCATEGORIES_BY_MAIN: Dict[str, Tuple[str, ...]] = {
    "Home": (
        "Furniture",
        "Bedding",
        "Bath",
        "Decor",
        "Storage",
        "Cleaning",
        "Appliances",
        "Scents",
        "Miscellaneous",
    ),
    "Garden": (
        "Seeds",
        "Plants",
        "Tools",
        "Watering",
        "Soil",
        "Groundcover",
        "Furniture",
        "Structures",
        "Miscellaneous",
    ),
    "Kitchen": (
        "Cookware",
        "Cutlery",
        "Utensils",
        "Cutting Boards",
        "Organization",
        "Appliances",
        "Tableware",
        "Storage",
        "Miscellaneous",
    ),
    "Home Improvement": (
        "Hand Tools",
        "Power Tools",
        "Paints/Finishes",
        "Materials",
        "Hardware",
        "Lighting",
        "Plumbing",
        "Electrical",
        "Miscellaneous",
    ),
    "Baggage": (
        "Checked",
        "Carry-On",
        "Backpacks",
        "Briefcases",
        "Purses",
        "Totes",
        "Wallets",
        "Organizers",
        "Miscellaneous",
    ),
    "Clothing": (
        "Tops",
        "Bottoms",
        "Dresses",
        "Outerwear",
        "Footwear",
        "Underwear",
        "Sleepwear",
        "Accessories",
        "Miscellaneous",
    ),
    "Wellness": (
        "Supplements",
        "Skin Care",
        "Oral Care",
        "Hygiene",
        "Fitness",
        "Sleep",
        "Air",
        "Lab Tests",
        "Miscellaneous",
    ),
    "Food": (
        "Fruits",
        "Vegetables",
        "Fish",
        "Meats",
        "Drinks",
        "Prepared",
        "Refrigerated",
        "Pantry",
        "Miscellaneous",
    ),
    "Children": (
        "Nursery",
        "Feeding",
        "Diapers",
        "Bath",
        "Gear",
        "Clothing",
        "Safety",
        "Toys",
        "Miscellaneous",
    ),
}

logger = logging.getLogger(__name__)

# Filled at app startup (and after DB seed) — same tree for ``GET /api/categories`` and nav templates.
_CATEGORIES_NAV_CACHE: Optional[List[dict]] = None


_LOGIN_LOCK = threading.Lock()
# Maps one-time sign-in code (6-char A–Z0–9) -> {"email": str, "expires": float}
_LOGIN_TOKENS: dict = {}
_EMAIL_CHANGE_LOCK = threading.Lock()
# code -> {"old_email": str, "new_email": str, "expires": float}
_EMAIL_CHANGE_CODES: dict = {}
_LOGIN_CODE_TTL_SEC = 600
_SIGN_IN_CODE_ALPHABET = string.ascii_uppercase + string.digits
_SIGN_IN_CODE_LEN = 6


def _normalize_login_email(s: str) -> str:
    return (s or "").strip().lower()


def _parse_login_email_field(v: str) -> str:
    s = _normalize_login_email(v)
    if not s or "@" not in s:
        raise ValueError("Enter a valid email address.")
    local, _, domain = s.partition("@")
    if not local or not domain or "@" in domain:
        raise ValueError("Enter a valid email address.")
    if "." not in domain:
        raise ValueError("Enter a valid email address.")
    return s


def _purge_expired_sign_in_tokens() -> None:
    now = time.time()
    with _LOGIN_LOCK:
        dead = [k for k, v in _LOGIN_TOKENS.items() if v["expires"] < now]
        for k in dead:
            del _LOGIN_TOKENS[k]


def _purge_expired_email_change_codes() -> None:
    now = time.time()
    with _EMAIL_CHANGE_LOCK:
        dead = [k for k, v in _EMAIL_CHANGE_CODES.items() if v["expires"] < now]
        for k in dead:
            del _EMAIL_CHANGE_CODES[k]


def _public_app_base_url() -> str:
    """Absolute site URL for links in emails (no trailing slash)."""
    raw = (os.environ.get("TABBED_PUBLIC_BASE_URL") or "http://127.0.0.1:8000").strip().rstrip("/")
    return raw or "http://127.0.0.1:8000"


def _username_base_from_email(email: str) -> str:
    local = email.split("@", 1)[0]
    base = re.sub(r"[^a-z0-9]+", "_", local.lower())
    base = re.sub(r"_+", "_", base).strip("_")
    if not base:
        base = "member"
    base = base[:28].rstrip("_")
    return base or "member"


def _allocate_unique_username(db: Session, email: str) -> str:
    base = _username_base_from_email(email)
    for attempt in range(48):
        cand = base if attempt == 0 else f"{base}_{secrets.token_hex(3)}"
        cand = cand[:40]
        if not db.query(User).filter(User.username == cand).first():
            return cand
    return f"u_{secrets.token_hex(10)}"


def _parse_chosen_username(raw: str) -> str:
    s = (raw or "").strip().lower()
    if len(s) < 3 or len(s) > 30:
        raise ValueError("Username must be between 3 and 30 characters.")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", s):
        raise ValueError(
            "Use 3–30 characters: start with a letter; then letters, numbers, or underscores only."
        )
    if s in _RESERVED_PUBLIC_USERNAMES:
        raise ValueError("That username is reserved. Please choose another.")
    return s


class LoginSendLinkBody(BaseModel):
    email: str = Field(..., min_length=1, max_length=320)

    @field_validator("email")
    @classmethod
    def email_ok(cls, v: str) -> str:
        return _parse_login_email_field(v)


class LoginVerifyCodeBody(BaseModel):
    email: str = Field(..., min_length=1, max_length=320)
    code: str = Field(..., min_length=1, max_length=32)

    @field_validator("email")
    @classmethod
    def email_ok(cls, v: str) -> str:
        return _parse_login_email_field(v)

    @field_validator("code")
    @classmethod
    def code_ok(cls, v: str) -> str:
        raw = (v or "").strip().upper().replace(" ", "")
        if len(raw) != _SIGN_IN_CODE_LEN or not re.fullmatch(
            r"[A-Z0-9]{6}", raw
        ):
            raise ValueError("Enter the 6-character code from your email.")
        return raw


class CompleteUsernameSetupBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)

    @field_validator("username")
    @classmethod
    def username_ok(cls, v: str) -> str:
        return _parse_chosen_username(v)


class ChangeUsernameBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)

    @field_validator("username")
    @classmethod
    def username_ok(cls, v: str) -> str:
        return _parse_chosen_username(v)


class EmailChangeRequestBody(BaseModel):
    new_email: str = Field(..., min_length=1, max_length=320)

    @field_validator("new_email")
    @classmethod
    def email_ok(cls, v: str) -> str:
        return _parse_login_email_field(v)


class EmailChangeConfirmBody(BaseModel):
    new_email: str = Field(..., min_length=1, max_length=320)
    code: str = Field(..., min_length=1, max_length=32)

    @field_validator("new_email")
    @classmethod
    def email_ok(cls, v: str) -> str:
        return _parse_login_email_field(v)

    @field_validator("code")
    @classmethod
    def code_ok(cls, v: str) -> str:
        raw = (v or "").strip().upper().replace(" ", "")
        if len(raw) != _SIGN_IN_CODE_LEN or not re.fullmatch(
            r"[A-Z0-9]{6}", raw
        ):
            raise ValueError("Enter the 6-character code from your email.")
        return raw


class FavoriteWriteBody(BaseModel):
    product_id: int = Field(..., ge=1)
    favorited: bool


class AdminFinishSignInBody(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)
    password: str = Field(default="")


class AdminVocabNameBody(BaseModel):
    """Admin: add a Made With / Made Without / Features label to the canonical vocabulary table."""

    name: str = Field(..., min_length=1, max_length=512)

    @field_validator("name", mode="after")
    @classmethod
    def strip_name(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("Name is required.")
        return s


def _normalize_admin_bulk_delete_ids(v: Any) -> list[int]:
    """Coerce JSON ids (int, float, str, or single scalar) before Pydantic validates list[int]."""
    if v is None:
        raise ValueError("Field ids is required")
    # bool is a subclass of int; never treat true/false as product ids.
    if isinstance(v, bool):
        raise ValueError("ids must be a non-empty list")
    if isinstance(v, (int, float, str)):
        v = [v]
    if not isinstance(v, (list, tuple)):
        raise ValueError("ids must be a non-empty list")
    if len(v) > 500:
        raise ValueError("At most 500 ids per request")
    out: list[int] = []
    seen: set[int] = set()
    for el in v:
        if el is True or el is False:
            raise ValueError("Invalid id (boolean not allowed)")
        n: int
        if isinstance(el, int):
            n = el
        elif isinstance(el, numbers.Integral):
            n = int(el)
        elif isinstance(el, float) and el.is_integer() and el == int(el):
            n = int(el)
        elif isinstance(el, str):
            s = (el or "").strip()
            if not s:
                raise ValueError("Each id must be a positive integer")
            try:
                n = int(s, 10)
            except ValueError as e:
                raise ValueError("Each id must be a positive integer") from e
        else:
            raise ValueError("Each id must be a positive integer")
        if n < 1:
            raise ValueError("Each id must be a positive integer")
        if n not in seen:
            seen.add(n)
            out.append(n)
    if not out:
        raise ValueError("ids must not be empty after validation")
    return out


class AdminBulkDeleteProductsBody(BaseModel):
    """Admin: delete one or more catalog products by id.

    Use ``list[Any]`` for ``ids`` so Pydantic does not run per-element ``int`` coercion (which
    can raise 422 for valid client payloads when combined with FastAPI's JSON parsing). Normalize
    to ``list[int]`` in an ``after`` validator.
    """

    ids: list[Any] = Field(..., min_length=1, max_length=500)

    @field_validator("ids", mode="after")
    @classmethod
    def _ids_as_positive_ints(cls, v: Any) -> list[int]:
        return _normalize_admin_bulk_delete_ids(v)


class AdminProductAiPopulateBody(BaseModel):
    """Product page URL for AI-assisted add-product form fill (no DB write)."""

    url: str = Field(..., min_length=8, max_length=2048)

    @field_validator("url")
    @classmethod
    def _strip_and_require_http(cls, v: str) -> str:
        s = (v or "").strip()
        if not s.startswith("http://") and not s.startswith("https://"):
            raise ValueError("URL must start with http:// or https://")
        return s


CONTACT_CATEGORY_EMAILS = {
    "support": os.environ.get("TABBED_CONTACT_EMAIL_SUPPORT", "support@tabbed.shop"),
    "features": os.environ.get("TABBED_CONTACT_EMAIL_FEATURES", "features@tabbed.shop"),
    "partnerships": os.environ.get(
        "TABBED_CONTACT_EMAIL_PARTNERSHIPS", "partnerships@tabbed.shop"
    ),
}

CONTACT_CATEGORY_LABELS = {
    "support": "Support",
    "features": "Features",
    "partnerships": "Partnerships",
}

CONTACT_TOPIC_PAGE_ORDER = ("support", "features", "partnerships")

CONTACT_FROM_ADDRESS = (os.environ.get("TABBED_CONTACT_FROM") or "no-reply@tabs.shop").strip()

# Sign-in verification emails (must be a domain/address your SMTP provider allows).
AUTH_MAIL_FROM = (os.environ.get("TABBED_AUTH_FROM") or "no-reply@tabs.shop").strip()

# Admin panel: one-time magic link (emailed to ADMIN_ALLOWED_EMAIL) → session cookie.
# Optional second factor: non-empty TABBED_ADMIN_SITE_PASSWORD must match for send-link and link completion.
ADMIN_ALLOWED_EMAIL = (os.environ.get("TABBED_ADMIN_EMAIL") or "admin@tabbed.shop").strip().lower()
ADMIN_SITE_PASSWORD_ENV = "TABBED_ADMIN_SITE_PASSWORD"
ADMIN_SESSION_COOKIE = "tabbed_admin_session"
_ADMIN_SESSION_TTL_SEC = 7 * 24 * 3600
_ADMIN_AUTH_LOCK = threading.Lock()
_ADMIN_LINK_TTL_SEC = 900
# One-time URL token (url-safe) -> {"expires": float}
_ADMIN_LINK_TOKENS: Dict[str, dict] = {}


def _admin_signing_secret() -> bytes:
    raw = (os.environ.get("TABBED_ADMIN_SECRET") or "").strip()
    if not raw:
        raw = "dev-admin-secret-change-in-production"
    return raw.encode("utf-8")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + pad).encode("ascii"))


def _username_setup_signing_secret() -> bytes:
    raw = (
        os.environ.get("TABBED_SESSION_SECRET") or os.environ.get("TABBED_ADMIN_SECRET") or ""
    ).strip()
    if not raw:
        raw = "dev-username-setup-secret-change-in-production"
    return raw.encode("utf-8")


def _issue_username_setup_cookie(response, *, email: str, max_age: int = _USERNAME_SETUP_TTL_SEC) -> None:
    exp = int(time.time()) + max_age
    payload = json.dumps(
        {"exp": exp, "v": 1, "email": _normalize_login_email(email)},
        separators=(",", ":"),
    ).encode("utf-8")
    p_b64 = _b64url_encode(payload)
    sig = hmac.new(
        _username_setup_signing_secret(), p_b64.encode("ascii"), hashlib.sha256
    ).hexdigest()
    token = f"{p_b64}.{sig}"
    secure = _parse_form_bool(os.environ.get("TABBED_COOKIE_SECURE"))
    response.set_cookie(
        USERNAME_SETUP_COOKIE,
        token,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
        secure=secure,
    )


def _read_username_setup_email(request: Request) -> Optional[str]:
    raw = (request.cookies.get(USERNAME_SETUP_COOKIE) or "").strip()
    if not raw or "." not in raw:
        return None
    try:
        p_b64, sig = raw.rsplit(".", 1)
        exp_sig = hmac.new(
            _username_setup_signing_secret(), p_b64.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(exp_sig, sig):
            return None
        payload = json.loads(_b64url_decode(p_b64).decode("utf-8"))
        if int(payload.get("exp") or 0) < int(time.time()):
            return None
        email = _normalize_login_email(str(payload.get("email") or ""))
        return email or None
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _clear_username_setup_cookie(response) -> None:
    response.delete_cookie(USERNAME_SETUP_COOKIE, path="/")


def _issue_admin_session_cookie(response, *, max_age: int = _ADMIN_SESSION_TTL_SEC) -> None:
    exp = int(time.time()) + max_age
    payload = json.dumps(
        {"exp": exp, "v": 1, "email": ADMIN_ALLOWED_EMAIL},
        separators=(",", ":"),
    ).encode("utf-8")
    p_b64 = _b64url_encode(payload)
    sig = hmac.new(_admin_signing_secret(), p_b64.encode("ascii"), hashlib.sha256).hexdigest()
    token = f"{p_b64}.{sig}"
    secure = _parse_form_bool(os.environ.get("TABBED_COOKIE_SECURE"))
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
        secure=secure,
    )


def _admin_cookie_valid(raw: Optional[str]) -> bool:
    if not raw or "." not in raw:
        return False
    try:
        p_b64, sig = raw.rsplit(".", 1)
        exp_sig = hmac.new(
            _admin_signing_secret(), p_b64.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(exp_sig, sig):
            return False
        payload = json.loads(_b64url_decode(p_b64).decode("utf-8"))
        if str(payload.get("email") or "").lower() != ADMIN_ALLOWED_EMAIL:
            return False
        if int(payload.get("exp") or 0) < int(time.time()):
            return False
        return True
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return False


def _purge_expired_admin_links() -> None:
    now = time.time()
    with _ADMIN_AUTH_LOCK:
        dead = [k for k, v in _ADMIN_LINK_TOKENS.items() if v["expires"] < now]
        for k in dead:
            del _ADMIN_LINK_TOKENS[k]


def _admin_site_password_configured() -> bool:
    return bool((os.environ.get(ADMIN_SITE_PASSWORD_ENV) or "").strip())


def _admin_site_password_matches(candidate: Optional[str]) -> bool:
    if not _admin_site_password_configured():
        return True
    expected = (os.environ.get(ADMIN_SITE_PASSWORD_ENV) or "").encode("utf-8")
    got = (candidate or "").encode("utf-8")
    return secrets.compare_digest(got, expected)


def _admin_transactional_from_address() -> str:
    """From: header for admin sign-in email only. Override with TABBED_ADMIN_MAIL_FROM."""
    override = (os.environ.get("TABBED_ADMIN_MAIL_FROM") or "").strip()
    if override:
        return override
    return "rainey@tabbed.shop"


def _send_admin_sign_in_link_email(*, token: str) -> None:
    base = _public_app_base_url()
    link = f"{base}/admin/verify?token={quote(token, safe='')}"
    minutes = _ADMIN_LINK_TTL_SEC // 60
    body_text = (
        "Sign in to the admin panel by opening this link (one time only):\n\n"
        f"{link}\n\n"
        f"The link expires in {minutes} minutes. If you did not request this, ignore this email.\n"
    )
    _send_contact_smtp(
        to_email=ADMIN_ALLOWED_EMAIL,
        subject="Tabbed admin sign-in link",
        body_text=body_text,
        from_address=_admin_transactional_from_address(),
    )


async def require_admin_session(request: Request) -> None:
    if not _admin_cookie_valid(request.cookies.get(ADMIN_SESSION_COOKIE)):
        raise HTTPException(status_code=401, detail="Admin authentication required.")


def _smtp_timeout_seconds() -> float:
    try:
        return max(5.0, float(os.environ.get("TABBED_SMTP_TIMEOUT") or "30"))
    except ValueError:
        return 30.0


def _send_contact_smtp(
    *,
    to_email: str,
    subject: str,
    body_text: str,
    from_address: Optional[str] = None,
) -> None:
    """Send a plain-text email via SMTP. Requires TABBED_SMTP_HOST (and usually auth)."""
    host = (os.environ.get("TABBED_SMTP_HOST") or "").strip()
    if not host:
        raise RuntimeError(
            "Outgoing email is not configured on this server (missing TABBED_SMTP_HOST)."
        )

    port = int(os.environ.get("TABBED_SMTP_PORT") or "587")
    user = (os.environ.get("TABBED_SMTP_USER") or "").strip()
    password = (os.environ.get("TABBED_SMTP_PASSWORD") or "").strip()
    if user and not password:
        raise RuntimeError(
            "TABBED_SMTP_PASSWORD is empty but SMTP username is set. "
            "Put your Google App Password or Resend API key in .env next to app.py, then restart the server. "
            "If the value is in .env but still empty, an empty variable in your environment may have blocked it "
            "(Cursor/IDE run config, export, or Docker); with override=True in load_dotenv, .env should win after restart."
        )
    use_ssl = _parse_form_bool(os.environ.get("TABBED_SMTP_SSL"))
    use_tls = _parse_form_bool(os.environ.get("TABBED_SMTP_TLS", "true"))
    timeout = _smtp_timeout_seconds()
    sender = (from_address or CONTACT_FROM_ADDRESS).strip()
    if not sender:
        raise RuntimeError("From address is empty (set TABBED_AUTH_FROM or TABBED_CONTACT_FROM).")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(body_text)

    tls_ctx = ssl.create_default_context()

    def _connection_failed_message(exc: BaseException) -> str:
        hint = (
            f"Could not connect to {host!r} port {port} ({type(exc).__name__}: {exc}). "
            "Check TABBED_SMTP_HOST and TABBED_SMTP_PORT, VPN/firewall, and TLS settings: "
            "port 587 usually needs TABBED_SMTP_TLS=true and TABBED_SMTP_SSL=false; "
            "port 465 usually needs TABBED_SMTP_SSL=true and TABBED_SMTP_TLS=false."
        )
        return hint

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=tls_ctx) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                if use_tls:
                    smtp.starttls(context=tls_ctx)
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
    except (OSError, ssl.SSLError, socket.timeout) as e:
        logger.exception("SMTP connection failed host=%s port=%s ssl=%s tls=%s", host, port, use_ssl, use_tls)
        raise RuntimeError(_connection_failed_message(e)) from e
    except smtplib.SMTPAuthenticationError as e:
        logger.exception("SMTP authentication failed host=%s", host)
        raise RuntimeError(
            f"SMTP login was rejected ({e}). "
            "Check TABBED_SMTP_USER and TABBED_SMTP_PASSWORD in .env. "
            "For Resend: user must be `resend`, password must be your full API key (re_…), not empty."
        ) from e
    except smtplib.SMTPException as e:
        logger.exception("Contact SMTP rejected message")
        raise RuntimeError(f"The mail server rejected the message: {e}") from e


def _generate_sign_in_code() -> str:
    return "".join(secrets.choice(_SIGN_IN_CODE_ALPHABET) for _ in range(_SIGN_IN_CODE_LEN))


def _send_login_code_email(*, to_email: str, code: str) -> None:
    """Email a one-time code to complete sign-in."""
    minutes = _LOGIN_CODE_TTL_SEC // 60
    body_text = (
        "Sign in to Tabbed with this one-time code:\n\n"
        f"  {code}\n\n"
        f"Enter it on the site where you requested sign-in. It expires in {minutes} minutes "
        "and can only be used once.\n\n"
        "If you did not try to sign in, you can ignore this email.\n"
    )
    _send_contact_smtp(
        to_email=to_email,
        subject="Your Tabbed sign-in code",
        body_text=body_text,
        from_address=AUTH_MAIL_FROM,
    )


def _send_email_change_code_email(*, to_email: str, code: str) -> None:
    """Email a one-time code to verify a new account email address."""
    minutes = _LOGIN_CODE_TTL_SEC // 60
    body_text = (
        "Confirm this email for your Tabbed account with this one-time code:\n\n"
        f"  {code}\n\n"
        f"Enter it on the site where you requested the change. It expires in {minutes} minutes "
        "and can only be used once.\n\n"
        "If you did not request an email change, you can ignore this message.\n"
    )
    _send_contact_smtp(
        to_email=to_email,
        subject="Your Tabbed email verification code",
        body_text=body_text,
        from_address=AUTH_MAIL_FROM,
    )


def _migrate_user_to_new_email(db: Session, user: User, new_email: str) -> User:
    """Move primary key email and preserve username, favorites, and avatar."""
    new_email = _normalize_login_email(new_email)
    old_email = _normalize_login_email(user.email)
    if new_email == old_email:
        raise HTTPException(status_code=400, detail="That’s already your email.")
    if db.query(User).filter(User.email == new_email).first():
        raise HTTPException(status_code=400, detail="That email is already in use.")
    orig_username = user.username
    tmp_username = f"{orig_username[:35]}__m_{secrets.token_hex(5)}"
    if len(tmp_username) > 64:
        tmp_username = tmp_username[:64]
    user.username = tmp_username
    db.flush()
    new_u = User(
        email=new_email,
        username=orig_username,
        created_at=user.created_at,
        username_confirmed=user.username_confirmed,
        avatar_image=user.avatar_image,
        avatar_mime_type=user.avatar_mime_type,
        avatar_uploaded_at=getattr(user, "avatar_uploaded_at", None),
    )
    db.add(new_u)
    db.flush()
    db.query(UserFavorite).filter(UserFavorite.user_email == old_email).update(
        {UserFavorite.user_email: new_email}, synchronize_session=False
    )
    db.delete(user)
    db.commit()
    db.refresh(new_u)
    return new_u


def _ensure_user_for_magic_login(db: Session, email: str) -> Tuple[User, bool]:
    """Return (user, created_new) for this email."""
    email = _normalize_login_email(email)
    user = db.query(User).filter(User.email == email).first()
    if user:
        return user, False
    username_new = _allocate_unique_username(db, email)
    user = User(email=email, username=username_new, username_confirmed=False)
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
        return user, True
    except IntegrityError:
        db.rollback()
        user = db.query(User).filter(User.email == email).first()
        if user:
            return user, False
        logger.exception("Could not create or load user after magic link for %s", email)
        raise HTTPException(
            status_code=503,
            detail="Could not complete sign-in. Try again.",
        ) from None


def _contributor_username_from_request(request: Request) -> str:
    c = (request.cookies.get(CONTRIBUTOR_USERNAME_COOKIE) or "").strip()
    if c:
        return c
    return (os.environ.get("TABBED_DEFAULT_CONTRIBUTOR_USERNAME") or "").strip()


def _format_profile_joined_month_year(user: Optional[User]) -> str:
    """Same display string as ``profile_joined_month_year`` on /user/… pages (``%B %Y``)."""
    if user is None:
        return ""
    ca = getattr(user, "created_at", None)
    if not ca:
        return ""
    return ca.strftime("%B %Y")


def _user_from_request(db: Session, request: Request) -> Optional[User]:
    uname = (_contributor_username_from_request(request) or "").strip()
    if not uname:
        return None
    return (
        db.query(User)
        .filter(func.lower(User.username) == uname.lower())
        .first()
    )


def _profile_avatar_url(username: str) -> str:
    """Served from DB when present; GET handler redirects to static icon when absent."""
    u = (username or "").strip()
    if not u:
        return "/static/person.svg"
    return "/api/users/" + quote(u, safe="") + "/avatar"


def _normalize_uploaded_avatar(raw: bytes) -> Tuple[bytes, str]:
    """Validate image, square-crop center, resize, re-encode as JPEG."""
    try:
        im = Image.open(io.BytesIO(raw))
    except UnidentifiedImageError as e:
        raise ValueError("Invalid or unsupported image file.") from e
    im = im.convert("RGB")
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    im = im.crop((left, top, left + side, top + side))
    max_side = 512
    if side > max_side:
        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88, optimize=True)
    data = buf.getvalue()
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Image is too large after processing.")
    return data, "image/jpeg"


def _favorite_counts_by_main_category_for_user(
    db: Session, user_email: str
) -> Dict[str, int]:
    """Count favorites per product ``main_category`` for a profile owner."""
    email = (user_email or "").strip()
    if not email:
        return {}
    rows = (
        db.query(Product.main_category, func.count(UserFavorite.id))
        .join(UserFavorite, UserFavorite.product_id == Product.id)
        .filter(UserFavorite.user_email == email)
        .group_by(Product.main_category)
        .all()
    )
    out: Dict[str, int] = {}
    for main, cnt in rows:
        key = (main or "").strip()
        if key:
            out[key] = int(cnt)
    return out


def _profile_favorite_category_rows_for_user(
    db: Session, user: Optional[User],
) -> List[Dict[str, Any]]:
    """One row per canonical main category: slug, display name, favorite count."""
    counts: Dict[str, int] = {}
    if user is not None and getattr(user, "email", None):
        counts = _favorite_counts_by_main_category_for_user(db, user.email)
    return [
        {
            "slug": slug,
            "name": name,
            "count": int(counts.get(name, 0)),
        }
        for slug, name, _sort in CANONICAL_SHOP_CATEGORIES
    ]


def _attach_profile_favorite_counts_by_slug(out: Dict[str, Any]) -> None:
    """Slug → count for pairing with ``nav_categories`` on the profile favorites bar."""
    rows = out.get("profile_favorite_category_rows") or []
    out["profile_favorite_counts_by_slug"] = {
        str(r["slug"]): int(r.get("count", 0)) for r in rows
    }


_DEFAULT_PROFILE_SETTINGS: Dict[str, str] = {
    "favorites-visible": "yes",
    "articles-visible-profile": "yes",
    "articles-visible-feed": "yes",
    "payout-notifications": "yes",
    "article-purchase-notifications": "yes",
    "product-approval-notifications": "yes",
    "article-approval-notifications": "yes",
    "show-articles-approved": "yes",
    "show-products-approved": "yes",
    "show-earnings": "yes",
}


def _merged_profile_settings(user: Optional[User]) -> Dict[str, str]:
    out = dict(_DEFAULT_PROFILE_SETTINGS)
    raw = getattr(user, "profile_settings", None) if user is not None else None
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k not in out or v is None:
                continue
            s = str(v).strip().lower()
            if s in ("yes", "no"):
                out[k] = s
    return out


def _profile_hero_template_kwargs(
    db: Session, username: str, request: Optional[Request] = None
) -> Dict[str, Any]:
    username = (username or "").strip()
    if not username:
        raise HTTPException(status_code=404, detail="User not found")
    viewer = ""
    if request is not None:
        viewer = (_contributor_username_from_request(request) or "").strip()
    user = db.query(User).filter(User.username == username).first()
    if not user:
        user = (
            db.query(User)
            .filter(func.lower(User.username) == username.lower())
            .first()
        )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    canon_username = (user.username or "").strip()
    out: Dict[str, Any] = {
        "profile_username": canon_username,
        "profile_avatar_url": _profile_avatar_url(canon_username),
        "profile_has_custom_avatar": False,
        "profile_is_owner": bool(
            canon_username
            and viewer
            and viewer.lower() == canon_username.lower()
        ),
        "profile_joined_month_year": "",
        "profile_favorite_count": 0,
        "profile_owner_email": "",
        "profile_favorite_category_rows": [
            {"slug": slug, "name": name, "count": 0}
            for slug, name, _sort in CANONICAL_SHOP_CATEGORIES
        ],
        # Always set so username.html never sees an undefined (context processors only merge in).
        "profile_favorite_counts_by_slug": {},
        "profile_favorites_hidden_from_public": False,
    }
    _attach_profile_favorite_counts_by_slug(out)
    out["profile_has_custom_avatar"] = bool(
        getattr(user, "avatar_image", None) and getattr(user, "avatar_uploaded_at", None)
    )
    if out["profile_is_owner"]:
        out["profile_owner_email"] = user.email or ""
    n = (
        db.query(func.count(UserFavorite.id))
        .filter(UserFavorite.user_email == user.email)
        .scalar()
    )
    out["profile_joined_month_year"] = _format_profile_joined_month_year(user)
    out["profile_favorite_count"] = int(n or 0)
    out["profile_favorite_category_rows"] = _profile_favorite_category_rows_for_user(db, user)
    _attach_profile_favorite_counts_by_slug(out)
    merged = _merged_profile_settings(user)
    out["profile_favorites_hidden_from_public"] = bool(
        not out["profile_is_owner"] and merged.get("favorites-visible") != "yes"
    )
    if out["profile_favorites_hidden_from_public"]:
        out["profile_favorite_count"] = 0
        out["profile_favorite_category_rows"] = [
            {"slug": slug, "name": name, "count": 0}
            for slug, name, _sort in CANONICAL_SHOP_CATEGORIES
        ]
        _attach_profile_favorite_counts_by_slug(out)
    # Mobile hamburger "Joined …": same rule as profile hero when owner; else viewer's date from session.
    viewer_row = _user_from_request(db, request) if request is not None else None
    if out["profile_is_owner"]:
        out["mobile_hamburger_joined"] = out["profile_joined_month_year"]
    else:
        out["mobile_hamburger_joined"] = _format_profile_joined_month_year(viewer_row)
    return out


def _parse_form_bool(v: Optional[Union[str, bool]]) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in ("1", "true", "on", "yes")


def _validate_main_category(name: str) -> str:
    n = (name or "").strip()
    if n not in _CANONICAL_MAIN_CATEGORY_NAMES:
        raise ValueError(f"Invalid main category: {n!r}.")
    return n


def _normalize_subcategory(value: Optional[str]) -> str:
    return (value or "").strip()


def _alloc_unique_category_slug_orm(db: Session, base_slug: str) -> str:
    cand = base_slug
    n = 2
    while db.query(Category.id).filter(Category.slug == cand).first():
        cand = f"{base_slug}-{n}"
        n += 1
    return cand


def _validate_admin_subcategory_or_empty(db: Session, main_category: str, sub: str) -> str:
    """Non-empty subcategory must exist as a child row under the given main category name."""
    sub = _normalize_subcategory(sub)
    if not sub:
        return ""
    parent = (
        db.query(Category)
        .filter(Category.parent_id.is_(None), Category.name == main_category)
        .first()
    )
    if not parent:
        raise ValueError("Invalid main category.")
    exists = (
        db.query(Category.id)
        .filter(
            Category.parent_id == parent.id,
            Category.subcategory == sub,
        )
        .first()
    )
    if not exists:
        raise ValueError(
            f"Subcategory {sub!r} is not defined for {main_category!r}. Add it in Admin first."
        )
    return sub


def _ensure_canonical_category_rows(db: Session) -> None:
    """Upsert-safe: insert missing root ``categories`` rows from ``CANONICAL_SHOP_CATEGORIES``.

    Ensures URLs like ``/wellness`` and ``GET /api/categories/wellness/catalog`` resolve instead of 404 when
    the DB had been created without admin-seeded mains.
    """
    existing = {slug for (slug,) in db.query(Category.slug).all()}
    for slug, name, sort_order in CANONICAL_SHOP_CATEGORIES:
        if slug in existing:
            continue
        db.add(
            Category(
                slug=slug,
                name=name,
                sort_order=sort_order,
                parent_id=None,
                main_category=name,
                subcategory=None,
            )
        )


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    db = SessionLocal()
    try:
        _ensure_canonical_category_rows(db)
        db.commit()
        refresh_categories_navigation_cache(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    yield


app = FastAPI(docs_url=None, redoc_url=None, lifespan=_app_lifespan)

# Mount static files
base_dir = Path(__file__).parent


def _shop_categories_from_categories_table(db: Session) -> List[dict]:
    """Return top-level ``categories`` rows (``parent_id`` NULL) with child rows as ``subcategories``."""
    main_rows = db.execute(
        text(
            "SELECT id, slug, name FROM categories WHERE parent_id IS NULL "
            "ORDER BY sort_order ASC, name ASC"
        )
    ).fetchall()
    out: List[dict] = []
    for main_id, slug, name in main_rows:
        sub_rows = db.execute(
            text(
                "SELECT slug, name FROM categories WHERE parent_id = :pid "
                "ORDER BY sort_order ASC, name ASC"
            ),
            {"pid": main_id},
        ).fetchall()
        out.append(
            {
                "slug": slug,
                "name": name,
                "subcategories": [
                    {
                        "slug": row[0],
                        "name": row[1],
                        "path": _category_sub_url_segment(slug, row[0]),
                    }
                    for row in sub_rows
                ],
            }
        )
    return out


def refresh_categories_navigation_cache(db: Session) -> None:
    """Rebuild in-memory categories used by ``/api/categories`` and ``nav_categories``. Call after category DB changes."""
    global _CATEGORIES_NAV_CACHE
    _CATEGORIES_NAV_CACHE = _shop_categories_from_categories_table(db)


def _categories_nav_cached() -> List[dict]:
    """Tree from startup cache; lazy-loads once if lifespan did not run (e.g. some tests)."""
    global _CATEGORIES_NAV_CACHE
    if _CATEGORIES_NAV_CACHE is None:
        db = SessionLocal()
        try:
            refresh_categories_navigation_cache(db)
        finally:
            db.close()
    return _CATEGORIES_NAV_CACHE  # type: ignore[return-value]


def _nav_categories_context(request: Request) -> dict:
    """Inject nav categories — same snapshot as ``GET /api/categories`` (loaded at startup, not each request)."""
    return {"nav_categories": _categories_nav_cached()}


def _viewer_joined_month_year_context(request: Request) -> dict:
    """Month + year the signed-in contributor joined (mobile hamburger; same string as profile hero)."""
    db = SessionLocal()
    try:
        user = _user_from_request(db, request)
        return {"viewer_joined_month_year": _format_profile_joined_month_year(user)}
    finally:
        db.close()


templates = Jinja2Templates(
    directory=str(base_dir / "templates"),
    context_processors=[_nav_categories_context, _viewer_joined_month_year_context],
)


static_dir = base_dir / "static"
uploads_dir = base_dir / "uploads"
uploads_dir.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")
app.mount("/flags", StaticFiles(directory=base_dir / "flags"), name="flags")
app.mount("/certifications", StaticFiles(directory=base_dir / "certifications"), name="certifications")


def _safe_fragment(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", (value or "").strip()).strip("_")
    return cleaned or "image"


def _looks_like_svg_bytes(data: bytes) -> bool:
    if not data or len(data) < 8:
        return False
    head = data.lstrip()[:8000]
    if b"<svg" in head[:200].lower():
        return True
    low = head.lower()
    return b"<?xml" in low[:200] and b"<svg" in low


def _svg_bytes_to_pil(data: bytes) -> Image.Image:
    """Rasterize SVG via PyMuPDF (no system Cairo required)."""
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise ValueError("SVG uploads require the pymupdf package.") from e
    try:
        with fitz.open(stream=data, filetype="svg") as doc:
            page = doc[0]
            pix = page.get_pixmap(alpha=True, dpi=144)
            png_bytes = pix.tobytes("png")
    except Exception as e:
        raise ValueError("Could not render SVG (invalid or unsupported).") from e
    im = Image.open(io.BytesIO(png_bytes))
    im.load()
    return im


def _looks_like_webp_bytes(data: bytes) -> bool:
    """RIFF container with WEBP fourCC (common WebP files)."""
    if not data or len(data) < 12:
        return False
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _decode_webp_via_dwebp_to_pil(data: bytes) -> Optional[Image.Image]:
    """Decode WebP using ``dwebp`` (Google libwebp CLI) when Pillow has no WEBP decoder."""
    exe = shutil.which("dwebp")
    if not exe or not data:
        return None
    try:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tin = Path(tmp) / "in.webp"
            tout = Path(tmp) / "out.png"
            tin.write_bytes(data)
            subprocess.run(
                [exe, str(tin), "-o", str(tout)],
                check=True,
                capture_output=True,
                timeout=60,
            )
            if not tout.is_file():
                return None
            raw_png = tout.read_bytes()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    try:
        im = Image.open(io.BytesIO(raw_png))
        im.load()
        return im
    except UnidentifiedImageError:
        return None


def _decode_upload_bytes_to_pil(data: bytes) -> Image.Image:
    """Decode raster bytes or SVG → raster via cairosvg."""
    if not data or len(data) < 4:
        raise ValueError("Invalid image data.")
    if _looks_like_svg_bytes(data):
        return _svg_bytes_to_pil(data)
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        return im
    except UnidentifiedImageError:
        if _looks_like_webp_bytes(data):
            dec = _decode_webp_via_dwebp_to_pil(data)
            if dec is not None:
                return dec
            raise ValueError(
                "Could not decode WebP. Install Pillow built with libwebp, or install "
                "Google's libwebp tools so `dwebp` is on PATH (e.g. `brew install webp`)."
            ) from None
        try:
            return _svg_bytes_to_pil(data)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError("Could not read image file.") from e


def _trim_logo_whitespace(im: Image.Image) -> Image.Image:
    """Crop transparent margins; for opaque images, trim near-white borders."""
    im = im.convert("RGBA")
    alpha = im.split()[3]
    bbox = alpha.getbbox()
    if bbox:
        return im.crop(bbox)
    rgb = Image.alpha_composite(
        Image.new("RGBA", im.size, (255, 255, 255, 255)), im
    ).convert("RGB")
    px = rgb.load()
    w, h = rgb.size
    t = 248
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if r < t or g < t or b < t or (r + g + b) < 736:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    if max_x < min_x:
        return im
    return im.crop((min_x, min_y, max_x + 1, max_y + 1))


def _trim_product_alpha_whitespace(im: Image.Image) -> Image.Image:
    """Crop transparent margins only (opaque product photos unchanged)."""
    im = im.convert("RGBA")
    bbox = im.split()[3].getbbox()
    return im.crop(bbox) if bbox else im


def _neutralize_fully_transparent_rgb(im: Image.Image) -> Image.Image:
    """Set RGB to white where alpha is 0 so resamplers never blend toward black."""
    im = im.convert("RGBA")
    arr = np.array(im, copy=True)
    m = arr[:, :, 3] == 0
    arr[m, 0] = 255
    arr[m, 1] = 255
    arr[m, 2] = 255
    return Image.fromarray(arr, mode="RGBA")


def _resize_rgba_to_height(im: Image.Image, target_h: int) -> Image.Image:
    """Resize to target height (badges / brand logos).

    Fully transparent pixels are set to white RGB first so resampling does not blend toward black.
    We use Pillow's standard RGBA LANCZOS (same family of scaling as browsers/CSS for images), not a
    separate premultiplied pipeline—our premultiplied path produced visibly different results from
    the admin file preview (object URL) for WebP/PNG with transparency.
    """
    im = _neutralize_fully_transparent_rgb(im.convert("RGBA"))
    w, h = im.size
    if h <= 0 or target_h <= 0:
        raise ValueError("Invalid image dimensions.")
    if h == target_h:
        return im
    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:  # Pillow < 9
        resample = Image.LANCZOS  # type: ignore[attr-defined]

    while h > target_h * 2:
        nh = max(target_h, h // 2)
        nw = max(1, int(round(w * nh / float(h))))
        im = im.resize((nw, nh), resample)
        w, h = im.size
    new_w = max(1, int(round(w * target_h / float(h))))
    return im.resize((new_w, target_h), resample).convert("RGBA")


def _encode_logo_blob_png(im: Image.Image) -> bytes:
    """Lossless PNG for brand/cert badges (avoids JPEG blocking on small logos)."""
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True, compress_level=6)
    return buf.getvalue()


def _whiten_non_product_pixels(image: Image.Image, threshold: int = 95) -> Image.Image:
    rgb_image = image.convert("RGB")
    pixels = rgb_image.load()
    width, height = rgb_image.size

    if width == 0 or height == 0:
        return rgb_image

    border_pixels = []
    for x in range(width):
        border_pixels.append(pixels[x, 0])
        border_pixels.append(pixels[x, height - 1])
    for y in range(height):
        border_pixels.append(pixels[0, y])
        border_pixels.append(pixels[width - 1, y])

    reds = sorted(c[0] for c in border_pixels)
    greens = sorted(c[1] for c in border_pixels)
    blues = sorted(c[2] for c in border_pixels)
    median_index = len(border_pixels) // 2
    background_rgb = (reds[median_index], greens[median_index], blues[median_index])

    threshold_sq = threshold * threshold
    candidates = [[False] * width for _ in range(height)]
    for y in range(height):
        row = candidates[y]
        for x in range(width):
            r, g, b = pixels[x, y]
            dr = r - background_rgb[0]
            dg = g - background_rgb[1]
            db = b - background_rgb[2]
            row[x] = (dr * dr + dg * dg + db * db) <= threshold_sq

    visited = [[False] * width for _ in range(height)]
    queue = deque()

    for x in range(width):
        if candidates[0][x] and not visited[0][x]:
            visited[0][x] = True
            queue.append((x, 0))
        if candidates[height - 1][x] and not visited[height - 1][x]:
            visited[height - 1][x] = True
            queue.append((x, height - 1))

    for y in range(height):
        if candidates[y][0] and not visited[y][0]:
            visited[y][0] = True
            queue.append((0, y))
        if candidates[y][width - 1] and not visited[y][width - 1]:
            visited[y][width - 1] = True
            queue.append((width - 1, y))

    while queue:
        x, y = queue.popleft()
        for nx, ny in (
            (x + 1, y),
            (x - 1, y),
            (x, y + 1),
            (x, y - 1),
            (x + 1, y + 1),
            (x + 1, y - 1),
            (x - 1, y + 1),
            (x - 1, y - 1),
        ):
            if (
                0 <= nx < width
                and 0 <= ny < height
                and candidates[ny][nx]
                and not visited[ny][nx]
            ):
                visited[ny][nx] = True
                queue.append((nx, ny))

    expanded = [row[:] for row in visited]
    fringe_offsets = (
        (1, 0), (-1, 0), (0, 1), (0, -1),
        (1, 1), (1, -1), (-1, 1), (-1, -1),
        (2, 0), (-2, 0), (0, 2), (0, -2),
    )
    for y in range(height):
        for x in range(width):
            if visited[y][x]:
                for dx, dy in fringe_offsets:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height:
                        expanded[ny][nx] = True

    for y in range(height):
        for x in range(width):
            if expanded[y][x]:
                pixels[x, y] = (255, 255, 255)

    return rgb_image


def _normalize_product_image_bytes(
    data: bytes, *, whiten_non_product: bool = False
) -> Optional[bytes]:
    """Run the same normalization as saving a product photo; return JPEG bytes (no disk write).

    ``whiten_non_product`` enables an optional flood-fill step that paints near-background
    pixels pure white (legacy behavior for very messy JPEG halos). It is off by default
    because it can damage real product/label art. Set ``whiten_non_product=True`` only if
    you explicitly want that pass.
    """
    if not data:
        return None
    im = _decode_upload_bytes_to_pil(data)
    if getattr(im, "n_frames", 1) > 1:
        im.seek(0)
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass
    im = _trim_product_alpha_whitespace(im)
    rgba = _neutralize_fully_transparent_rgb(im.convert("RGBA"))
    bg = Image.new("RGB", rgba.size, (255, 255, 255))
    bg.paste(rgba, mask=rgba.split()[3])
    normalized = _whiten_non_product_pixels(bg) if whiten_non_product else bg
    buf = io.BytesIO()
    normalized.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _save_normalized_upload(upload: UploadFile, prefix: str, label: str):
    """Store product images on disk as normalized JPEG (raster, SVG, WebP, PNG, GIF)."""
    if not upload or not upload.filename:
        return None, None

    safe_label = _safe_fragment(label)
    original_name = Path(upload.filename).name
    stem = _safe_fragment(Path(original_name).stem)
    filename = f"{prefix}_{safe_label}_{stem}.jpg"
    output_path = uploads_dir / filename

    upload.file.seek(0)
    data = upload.file.read()
    if not data:
        return None, None
    jpeg_bytes = _normalize_product_image_bytes(data)
    if not jpeg_bytes:
        return None, None
    output_path.write_bytes(jpeg_bytes)
    return jpeg_bytes, filename


def _normalize_upload_to_blob(upload: UploadFile, label: str) -> bytes:
    """Certification badge: same pipeline as brand logos (fixed height PNG, trimmed)."""
    _ = label
    if not upload or not upload.filename:
        raise ValueError("Image is required.")
    upload.file.seek(0)
    data = upload.file.read()
    if not data:
        raise ValueError("Image is required.")
    return _normalize_brand_image_bytes(data)


# Stored in ``brands.image`` / ``certifications.image``: fixed height, width from aspect ratio.
# Product UIs may display smaller via CSS; server can downscale for cards separately later.
_BRAND_LOGO_TARGET_HEIGHT_PX = 64


def _normalize_brand_image_bytes(data: bytes) -> bytes:
    """Decode raster/SVG, trim whitespace, resize to ``_BRAND_LOGO_TARGET_HEIGHT_PX`` height, store as lossless PNG."""
    im = _decode_upload_bytes_to_pil(data)

    if getattr(im, "n_frames", 1) > 1:
        im.seek(0)

    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass

    im = im.convert("RGBA")
    im = _trim_logo_whitespace(im)

    w, h = im.size
    if h <= 0 or w <= 0:
        raise ValueError("Invalid image dimensions.")

    resized = _resize_rgba_to_height(im, _BRAND_LOGO_TARGET_HEIGHT_PX)
    return _encode_logo_blob_png(resized)


def _normalize_brand_upload_to_blob(upload: UploadFile, label: str) -> bytes:
    """Resize brand logo to ``_BRAND_LOGO_TARGET_HEIGHT_PX`` height; width scales with aspect ratio."""
    if not upload or not upload.filename:
        raise ValueError("Image is required.")

    _ = label  # reserved for logging / future use
    upload.file.seek(0)
    data = upload.file.read()
    if not data:
        raise ValueError("Image is required.")
    return _normalize_brand_image_bytes(data)


def _normalize_brand_legacy_blob_or_keep(raw: Optional[bytes]) -> Optional[bytes]:
    """Best-effort target-height raster for legacy BLOBs; keep original bytes if decode fails."""
    if raw is None:
        return None
    try:
        return _normalize_brand_image_bytes(bytes(raw))
    except Exception as e:
        logger.warning("Could not normalize legacy brand image bytes, storing raw: %s", e)
        return bytes(raw)


def _admin_resolve_brand(
    db: Session, brand_name: str, brand_image: Optional[UploadFile]
) -> Brand:
    name = (brand_name or "").strip()
    if not name:
        raise ValueError("Brand name is required.")
    b = db.query(Brand).filter(Brand.name == name).first()
    if b is None:
        blob = None
        if brand_image is not None and getattr(brand_image, "filename", None):
            blob = _normalize_brand_upload_to_blob(brand_image, label=name)
        b = Brand(name=name, image=blob)
        db.add(b)
        db.flush()
        return b
    if brand_image is not None and getattr(brand_image, "filename", None):
        b.image = _normalize_brand_upload_to_blob(brand_image, label=name)
    return b


def _admin_apply_product_certifications(
    db: Session,
    product: Product,
    meta_list: List[Any],
    cert_files: Optional[List[UploadFile]],
) -> None:
    files = [f for f in (cert_files or []) if f is not None and getattr(f, "filename", None)]
    fi = 0
    ordered_ids: List[int] = []
    for raw in meta_list:
        if isinstance(raw, dict):
            cid = raw.get("id")
            name = str(raw.get("name") or "").strip()
            replace = bool(raw.get("replace_image"))
        else:
            d = _normalize_cert_dict(raw)
            if not d:
                continue
            cid = None
            name = d["name"]
            replace = False
        cert: Optional[Certification] = None
        if cid is not None:
            try:
                cert = db.query(Certification).filter(
                    Certification.id == int(cid)
                ).first()
            except (TypeError, ValueError):
                cert = None
        if cert is None and name:
            cert = db.query(Certification).filter(Certification.name == name).first()
        if cert is None and name:
            cert = Certification(name=name, image=None)
            db.add(cert)
            db.flush()
        if cert is None:
            continue
        if replace and fi < len(files):
            cert.image = _normalize_upload_to_blob(files[fi], label=cert.name)
            fi += 1
        ordered_ids.append(cert.id)
    seen: Set[int] = set()
    final_ids: List[int] = []
    for i in ordered_ids:
        if i not in seen:
            seen.add(i)
            final_ids.append(i)
    rows = (
        db.query(Certification).filter(Certification.id.in_(final_ids)).all()
        if final_ids
        else []
    )
    by_id = {c.id: c for c in rows}
    product.certifications = [by_id[i] for i in final_ids if i in by_id]


def _add_product(
    db: Session,
    *,
    product_name: str,
    brand_row: Brand,
    main_category: str,
    subcategory: str,
    made_in: str,
    price: float,
    product_link: Optional[str],
    earns_commission: bool,
    made_with_list: List[Any],
    made_without_list: List[Any],
    attributes_list: List[Any],
    description: Optional[str],
    product_image_data,
    product_image_filename: Optional[str],
    is_verified: bool = False,
) -> Product:
    db_product = Product(
        product_name=product_name,
        brand_id=brand_row.id,
        main_category=main_category,
        subcategory=subcategory,
        made_in=made_in,
        price=price,
        product_link=product_link,
        earns_commission=bool(earns_commission),
        made_with=made_with_list,
        made_without=made_without_list,
        attributes=attributes_list,
        description=description,
        product_image=product_image_data,
        product_image_filename=product_image_filename,
        is_verified=bool(is_verified),
    )
    db.add(db_product)
    db.flush()
    return db_product


def _coerce_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        if not value.strip():
            return []
        try:
            parsed = json.loads(value)
            return [str(x) for x in parsed] if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _vocab_get_or_create_name(db: Session, model, name: str) -> str:
    """Return canonical label from *model*; create row with *name* if no case-insensitive match exists."""
    n = (name or "").strip()
    if not n:
        return ""
    ex = (
        db.query(model)
        .filter(func.lower(model.name) == func.lower(n))
        .first()
    )
    if ex:
        return ex.name
    try:
        with db.begin_nested():
            row = model(name=n)
            db.add(row)
        return n
    except IntegrityError:
        ex2 = (
            db.query(model)
            .filter(func.lower(model.name) == func.lower(n))
            .first()
        )
        return ex2.name if ex2 else n


def _normalize_product_tag_lists_to_vocab(
    db: Session,
    made_with: List[Any],
    made_without: List[Any],
    attributes: List[Any],
) -> Tuple[List[str], List[str], List[str]]:
    out_mw: List[str] = []
    out_mo: List[str] = []
    out_at: List[str] = []
    seenw: Set[str] = set()
    seeno: Set[str] = set()
    seena: Set[str] = set()
    for x in _coerce_str_list(made_with):
        c = _vocab_get_or_create_name(db, VocabMadeWith, x)
        if c and c.lower() not in seenw:
            seenw.add(c.lower())
            out_mw.append(c)
    for x in _coerce_str_list(made_without):
        c = _vocab_get_or_create_name(db, VocabMadeWithout, x)
        if c and c.lower() not in seeno:
            seeno.add(c.lower())
            out_mo.append(c)
    for x in _coerce_str_list(attributes):
        c = _vocab_get_or_create_name(db, VocabFeature, x)
        if c and c.lower() not in seena:
            seena.add(c.lower())
            out_at.append(c)
    return out_mw, out_mo, out_at


def _normalize_cert_dict(item: Any) -> Optional[dict]:
    if item is None:
        return None
    if isinstance(item, str):
        name = item.strip()
        if not name:
            return None
        return {"name": name, "link": None, "image_filename": None}
    if isinstance(item, dict):
        name = str(item.get("name") or "").strip()
        if not name:
            return None
        link = item.get("link")
        link_s = (str(link).strip() if link is not None else "") or None
        fn = item.get("image_filename")
        fn_s = (str(fn).strip() if fn is not None else "") or None
        return {"name": name, "link": link_s, "image_filename": fn_s}
    return None


def _certifications_payload(product: Product) -> List[dict]:
    rows = sorted(
        getattr(product, "certifications", None) or [],
        key=lambda c: int(c.id),
    )
    out: List[dict] = []
    for c in rows:
        link_s = (getattr(c, "link", None) or "").strip()
        out.append(
            {
                "id": c.id,
                "name": c.name,
                "link": link_s or None,
                "image_filename": None,
                "image_url": (
                    f"/api/certifications/{c.id}/image" if c.image else None
                ),
            }
        )
    return out


def _certification_names_for_product(product: Product) -> List[str]:
    return [
        (x.name or "").strip()
        for x in (product.certifications or [])
        if (x.name or "").strip()
    ]


def _category_for_shop_path(db: Session, segment: str) -> Optional[Category]:
    """Resolve a category from a URL segment: exact slug, then case-insensitive slug or display name."""
    raw = unquote((segment or "").strip())
    if not raw:
        return None
    cat = db.query(Category).filter(Category.slug == raw).first()
    if cat:
        return cat
    cat = db.query(Category).filter(func.lower(Category.slug) == raw.lower()).first()
    if cat:
        return cat
    return (
        db.query(Category)
        .filter(func.lower(Category.name) == raw.lower(), Category.parent_id.is_(None))
        .first()
    )


def _category_sub_url_segment(main_slug: str, child_slug: str) -> str:
    """URL segment for a subcategory path ``/{main}/{segment}`` from DB ``child_slug``."""
    main_slug = (main_slug or "").strip()
    child_slug = (child_slug or "").strip()
    if not main_slug or not child_slug:
        return child_slug
    prefix = f"{main_slug}-"
    if child_slug.startswith(prefix):
        return child_slug[len(prefix) :]
    return child_slug


def _category_for_nested_shop_path(
    db: Session, main_slug: str, sub_slug: str
) -> Optional[Category]:
    """Resolve subcategory from ``/main_slug/sub_slug`` (segment after parent slug prefix in DB)."""
    main_slug = unquote((main_slug or "").strip())
    sub_slug = unquote((sub_slug or "").strip())
    if not main_slug or not sub_slug:
        return None
    parent = (
        db.query(Category)
        .filter(Category.parent_id.is_(None), Category.slug == main_slug)
        .first()
    )
    if not parent:
        return None
    for ch in db.query(Category).filter(Category.parent_id == parent.id).all():
        if _category_sub_url_segment(main_slug, ch.slug) == sub_slug:
            return ch
    return None


_CATEGORY_SHOP_RESERVED_MAIN_SLUGS = frozenset(
    {
        "all",
        "about",
        "articles",
        "contact",
        "user",
        "admin",
        "api",
        "search",
        "login",
        "logout",
        "landing",
        "contribute",
        "terms",
        "privacy-policy",
        "privacy",
        "affiliate-disclosure",
        "faq",
        "partner-with-us",
        "partners",
        "sign-in",
        "static",
    }
)

_RESERVED_PUBLIC_USERNAMES |= set(_CANONICAL_CATEGORY_SLUGS)
_RESERVED_PUBLIC_USERNAMES |= set(_CATEGORY_SHOP_RESERVED_MAIN_SLUGS)


def _category_hrefs_for_product(db: Session, product: Product) -> Tuple[Optional[str], Optional[str]]:
    """Shop URLs for main shelf and subcategory, or (None, None) if unknown."""
    main = (getattr(product, "main_category", None) or "").strip()
    if not main:
        return None, None
    parent = (
        db.query(Category)
        .filter(Category.parent_id.is_(None), Category.name == main)
        .first()
    )
    if not parent:
        parent = (
            db.query(Category)
            .filter(
                Category.parent_id.is_(None),
                func.lower(Category.name) == main.lower(),
            )
            .first()
        )
    if not parent:
        parent = (
            db.query(Category)
            .filter(Category.parent_id.is_(None), Category.slug == main)
            .first()
        )
    if not parent:
        parent = (
            db.query(Category)
            .filter(
                Category.parent_id.is_(None),
                func.lower(Category.slug) == main.lower(),
            )
            .first()
        )
    if not parent:
        return None, None
    main_href = f"/{parent.slug}"
    sub = _normalize_subcategory(getattr(product, "subcategory", None))
    if not sub:
        return main_href, None
    child = (
        db.query(Category)
        .filter(Category.parent_id == parent.id, Category.subcategory == sub)
        .first()
    )
    if not child:
        for ch in db.query(Category).filter(Category.parent_id == parent.id).all():
            if (_normalize_subcategory(ch.subcategory)).lower() == sub.lower():
                child = ch
                break
    if not child:
        for ch in db.query(Category).filter(Category.parent_id == parent.id).all():
            seg_try = _category_sub_url_segment(parent.slug, ch.slug)
            if seg_try and sub and seg_try.lower() == sub.lower():
                child = ch
                break
    if not child:
        return main_href, None
    seg = _category_sub_url_segment(parent.slug, child.slug)
    return main_href, f"/{parent.slug}/{seg}"


def _product_api_dict(product: Product, db: Optional[Session] = None) -> dict:
    sub = _normalize_subcategory(getattr(product, "subcategory", None))
    main = (getattr(product, "main_category", None) or "").strip()
    brand = getattr(product, "brand", None)
    brand_name = brand.name if brand else ""
    brand_link = (getattr(brand, "link", None) or "").strip() if brand else ""
    brand_img_url = (
        f"/api/brands/{brand.id}/image"
        if brand and brand.image and getattr(brand, "id", None)
        else None
    )
    out = {
        "id": product.id,
        "name": product.product_name,
        "brand_id": getattr(product, "brand_id", None),
        "brand_name": brand_name,
        "brand_link": brand_link,
        "brand": (
            {
                "id": brand.id,
                "name": brand.name,
                "image_url": brand_img_url,
                "link": brand_link,
            }
            if brand
            else None
        ),
        "main_category": main,
        "subcategory": sub,
        "category": main,
        "made_in": product.made_in,
        "price": product.price,
        "product_link": product.product_link,
        "earns_commission": bool(getattr(product, "earns_commission", False)),
        "made_with": _coerce_str_list(product.made_with),
        "made_without": _coerce_str_list(product.made_without),
        "attributes": _coerce_str_list(getattr(product, "attributes", None)),
        "certifications": _certifications_payload(product),
        "product_image_filename": product.product_image_filename,
        "brand_image_filename": None,
        "brand_image_url": brand_img_url,
        "description": product.description or "",
        "is_verified": bool(getattr(product, "is_verified", False)),
    }
    if db is not None:
        mh, sh = _category_hrefs_for_product(db, product)
        out["category_main_href"] = mh
        out["category_sub_href"] = sh
    else:
        out["category_main_href"] = None
        out["category_sub_href"] = None
    return out


def _facet_aggregation_from_products(products: List[Product]) -> dict:
    """Facet value lists + logo maps for a product set (category shelf or search results)."""
    brands = sorted({p.brand.name for p in products if p.brand})
    made_in = sorted({p.made_in for p in products if p.made_in})
    brand_logos: dict = {}
    for p in products:
        b = p.brand
        if b and b.name and b.image and b.name not in brand_logos:
            brand_logos[b.name] = f"/api/brands/{b.id}/image"
    certs: Set[str] = set()
    certification_images: dict = {}
    with_set: Set[str] = set()
    without_set: Set[str] = set()
    attr_set: Set[str] = set()
    for p in products:
        for cert_row in getattr(p, "certifications", None) or []:
            name = (getattr(cert_row, "name", None) or "").strip()
            if name:
                certs.add(name)
                if getattr(cert_row, "image", None) and name not in certification_images:
                    certification_images[name] = f"/api/certifications/{cert_row.id}/image"
        for c in _certification_names_for_product(p):
            t = c.strip()
            if t:
                certs.add(t)
        for s in _coerce_str_list(p.made_with):
            t = s.strip()
            if t:
                with_set.add(t)
        for s in _coerce_str_list(p.made_without):
            t = s.strip()
            if t:
                without_set.add(t)
        for s in _coerce_str_list(getattr(p, "attributes", None)):
            t = s.strip()
            if t:
                attr_set.add(t)
    return {
        "brands": brands,
        "made_in": made_in,
        "certifications": sorted(certs),
        "made_with": sorted(with_set),
        "made_without": sorted(without_set),
        "attributes": sorted(attr_set),
        "brand_logos": brand_logos,
        "certification_images": certification_images,
    }


def _category_catalog_payload(db: Session, category_path_segment: str) -> Optional[dict]:
    cat = _category_for_shop_path(db, category_path_segment)
    if not cat:
        return None
    q = db.query(Product).options(
        joinedload(Product.brand),
        selectinload(Product.certifications),
    )
    if cat.parent_id is None:
        products = q.filter(Product.main_category == cat.name).all()
    else:
        sub = (cat.subcategory or "").strip()
        products = (
            q.filter(Product.main_category == cat.main_category)
            .filter(Product.subcategory == sub)
            .all()
        )
    return {
        "category": {"slug": cat.slug, "name": cat.name},
        "products": [_product_api_dict(p, db) for p in products],
        "facets": _facet_aggregation_from_products(products),
    }


def _search_catalog_payload(db: Session, q: str) -> Optional[dict]:
    """Products + facets for shop UI: all rows matching search (no category filter)."""
    raw = (q or "").strip()
    if not raw:
        return None
    products = _products_matching_search_query(db, raw)
    return {
        "query": raw,
        "products": [_product_api_dict(p, db) for p in products],
        "facets": _facet_aggregation_from_products(products),
    }


def _all_products_list_response(db: Session) -> JSONResponse:
    """Return full catalog as JSON: one ``products`` array (same item shape as other product API responses)."""
    products = (
        db.query(Product)
        .options(joinedload(Product.brand), selectinload(Product.certifications))
        .all()
    )
    return JSONResponse(
        content={"products": [_product_api_dict(p, db) for p in products]},
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


def _all_catalog_payload(db: Session) -> dict:
    """Full catalog for shop UI: same keys as ``/api/products/category/{slug}`` (products + facets)."""
    products = (
        db.query(Product)
        .options(joinedload(Product.brand), selectinload(Product.certifications))
        .all()
    )
    return {
        "category": {"slug": "all", "name": "All categories"},
        "products": [_product_api_dict(p, db) for p in products],
        "facets": _facet_aggregation_from_products(products),
    }


@app.get("/")
async def read_root(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "nav_active": "",
        },
    )


@app.get("/landing")
async def landing_redirect():
    return RedirectResponse(url="/", status_code=307)

@app.get("/contribute")
async def contribute_removed_redirect():
    return RedirectResponse(url="/", status_code=301)


def _contact_hub_page(request: Request):
    raw = (request.query_params.get("topic") or "support").strip().lower()
    topic = raw if raw in CONTACT_CATEGORY_EMAILS else "support"
    contact_topics = [
        {
            "key": k,
            "label": CONTACT_CATEGORY_LABELS[k],
            "email": CONTACT_CATEGORY_EMAILS[k],
        }
        for k in CONTACT_TOPIC_PAGE_ORDER
        if k in CONTACT_CATEGORY_EMAILS
    ]
    return templates.TemplateResponse(
        "contact_hub.html",
        {
            "request": request,
            "nav_active": "contact",
            "contact_topic": topic,
            "contact_topics": contact_topics,
            "contact_alt_email": CONTACT_CATEGORY_EMAILS[topic],
        },
    )


@app.get("/about")
async def about_page(request: Request):
    return templates.TemplateResponse(
        "about.html",
        {"request": request, "nav_active": "about"},
    )


@app.get("/about/privacy-policy")
async def about_privacy_policy_page(request: Request):
    return templates.TemplateResponse(
        "privacy.html",
        {"request": request, "nav_active": "about"},
    )


@app.get("/about/affiliate-disclosure")
async def about_affiliate_disclosure_page(request: Request):
    return templates.TemplateResponse(
        "affiliate_disclosure.html",
        {"request": request, "nav_active": "about"},
    )


@app.get("/about/partner-with-us")
async def about_partner_page(request: Request):
    return templates.TemplateResponse(
        "partners.html",
        {"request": request, "nav_active": "about"},
    )


@app.get("/about/faq")
async def about_faq_page(request: Request):
    return templates.TemplateResponse(
        "faq.html",
        {"request": request, "nav_active": "about"},
    )


@app.get("/about/contact")
async def about_contact_hub_page(request: Request):
    return _contact_hub_page(request)


@app.get("/about/contact/support")
async def about_contact_support_page():
    return RedirectResponse(url="/about/contact?topic=support", status_code=302)


@app.get("/about/contact/features")
async def about_contact_features_page():
    return RedirectResponse(url="/about/contact?topic=features", status_code=302)


@app.get("/about/contact/partnerships")
async def about_contact_partnerships_page():
    return RedirectResponse(url="/about/contact?topic=partnerships", status_code=302)


@app.get("/partner-with-us")
async def partner_with_us_legacy_redirect():
    return RedirectResponse(url="/about/partner-with-us", status_code=301)


@app.get("/partners")
async def partners_legacy_redirect():
    return RedirectResponse(url="/about/partner-with-us", status_code=301)


@app.get("/contributor-policy")
async def contributor_policy_removed_redirect():
    return RedirectResponse(url="/about", status_code=301)


@app.get("/contact")
async def contact_page():
    return RedirectResponse(url="/about/contact", status_code=302)


@app.get("/contact/support")
async def contact_support_legacy_redirect():
    return RedirectResponse(url="/about/contact/support", status_code=301)


@app.get("/contact/features")
async def contact_features_legacy_redirect():
    return RedirectResponse(url="/about/contact/features", status_code=301)


@app.get("/contact/partnerships")
async def contact_partnerships_legacy_redirect():
    return RedirectResponse(url="/about/contact/partnerships", status_code=301)


@app.get("/login")
async def login_redirect():
    """Legacy URL: sign-in is handled in the site-wide modal."""
    return RedirectResponse(url="/?signin=1", status_code=302)


@app.post("/api/auth/send-sign-in-link")
async def api_send_sign_in_link(body: LoginSendLinkBody):
    email = body.email
    _purge_expired_sign_in_tokens()
    host = (os.environ.get("TABBED_SMTP_HOST") or "").strip()
    if not host:
        raise HTTPException(
            status_code=503,
            detail=(
                "Sign-in email is not configured. Set TABBED_SMTP_HOST (and credentials) "
                "in a .env file next to app.py—see .env.example."
            ),
        )
    with _LOGIN_LOCK:
        stale = [t for t, v in _LOGIN_TOKENS.items() if v.get("email") == email]
        for t in stale:
            del _LOGIN_TOKENS[t]
        code: Optional[str] = None
        for _ in range(48):
            cand = _generate_sign_in_code()
            if cand not in _LOGIN_TOKENS:
                code = cand
                _LOGIN_TOKENS[cand] = {
                    "email": email,
                    "expires": time.time() + _LOGIN_CODE_TTL_SEC,
                }
                break
    if not code:
        raise HTTPException(status_code=503, detail="Could not issue a sign-in code. Try again.")
    try:
        _send_login_code_email(to_email=email, code=code)
        logger.info("Sign-in code email sent to %s", email)
    except RuntimeError as e:
        with _LOGIN_LOCK:
            _LOGIN_TOKENS.pop(code, None)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {
        "ok": True,
        "message": "Check your email (and junk folder) for your 6-character sign-in code.",
    }


@app.post("/api/auth/verify-sign-in-code")
async def api_verify_sign_in_code(body: LoginVerifyCodeBody, db: Session = Depends(get_db)):
    email = _normalize_login_email(body.email)
    code = body.code
    _purge_expired_sign_in_tokens()
    with _LOGIN_LOCK:
        rec = _LOGIN_TOKENS.get(code)
    if not rec or rec["expires"] < time.time() or _normalize_login_email(rec.get("email") or "") != email:
        raise HTTPException(
            status_code=400,
            detail="That code is incorrect or has expired. Request a new code.",
        )
    user, _created = _ensure_user_for_magic_login(db, email)
    with _LOGIN_LOCK:
        _LOGIN_TOKENS.pop(code, None)
    if not user.username_confirmed:
        resp = JSONResponse({"ok": True, "needs_username": True})
        _issue_username_setup_cookie(resp, email=email)
        return resp
    username = user.username
    resp = JSONResponse({"ok": True, "needs_username": False, "username": username})
    resp.set_cookie(
        CONTRIBUTOR_USERNAME_COOKIE,
        username,
        max_age=60 * 60 * 24 * 365,
        path="/",
        samesite="lax",
    )
    return resp


@app.post("/api/auth/complete-username-setup")
async def api_complete_username_setup(
    request: Request, body: CompleteUsernameSetupBody, db: Session = Depends(get_db)
):
    email = _read_username_setup_email(request)
    if not email:
        raise HTTPException(
            status_code=401,
            detail="This step has expired. Close the window and sign in again.",
        )
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=400, detail="Account not found. Sign in again.")
    if user.username_confirmed:
        raise HTTPException(status_code=400, detail="Username is already set.")
    chosen = body.username
    taken = (
        db.query(User)
        .filter(User.username == chosen, User.email != email)
        .first()
    )
    if taken:
        raise HTTPException(status_code=400, detail="That username is already taken.")
    user.username = chosen
    user.username_confirmed = True
    db.commit()
    resp = JSONResponse({"ok": True, "username": chosen})
    _clear_username_setup_cookie(resp)
    resp.set_cookie(
        CONTRIBUTOR_USERNAME_COOKIE,
        chosen,
        max_age=60 * 60 * 24 * 365,
        path="/",
        samesite="lax",
    )
    return resp


@app.get("/api/me/session-hints")
async def api_me_session_hints(request: Request, db: Session = Depends(get_db)):
    """Lightweight session facts for client UI (e.g. mobile menu joined date)."""
    user = _user_from_request(db, request)
    return JSONResponse({"joined_month_year": _format_profile_joined_month_year(user)})


@app.post("/api/me/username")
async def api_change_my_username(
    request: Request,
    body: ChangeUsernameBody,
    db: Session = Depends(get_db),
):
    user = _user_from_request(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required")
    chosen = body.username
    if chosen == (user.username or "").strip().lower():
        return {"ok": True, "username": user.username}
    taken = (
        db.query(User)
        .filter(User.username == chosen, User.email != user.email)
        .first()
    )
    if taken:
        raise HTTPException(status_code=400, detail="That username is already taken.")
    user.username = chosen
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400, detail="That username is already taken."
        ) from None
    resp = JSONResponse({"ok": True, "username": chosen})
    resp.set_cookie(
        CONTRIBUTOR_USERNAME_COOKIE,
        chosen,
        max_age=60 * 60 * 24 * 365,
        path="/",
        samesite="lax",
    )
    return resp


@app.post("/api/auth/request-email-change")
async def api_request_email_change(
    body: EmailChangeRequestBody,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _user_from_request(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required")
    new_email = _normalize_login_email(body.new_email)
    old_email = _normalize_login_email(user.email)
    if new_email == old_email:
        raise HTTPException(status_code=400, detail="That’s already your email.")
    if db.query(User).filter(User.email == new_email).first():
        raise HTTPException(status_code=400, detail="That email is already in use.")
    host = (os.environ.get("TABBED_SMTP_HOST") or "").strip()
    if not host:
        raise HTTPException(
            status_code=503,
            detail=(
                "Email is not configured. Set TABBED_SMTP_HOST in a .env file next to app.py."
            ),
        )
    _purge_expired_email_change_codes()
    code: Optional[str] = None
    with _EMAIL_CHANGE_LOCK:
        stale = [t for t, v in _EMAIL_CHANGE_CODES.items() if v.get("new_email") == new_email]
        for t in stale:
            del _EMAIL_CHANGE_CODES[t]
        for _ in range(48):
            cand = _generate_sign_in_code()
            if cand in _LOGIN_TOKENS or cand in _EMAIL_CHANGE_CODES:
                continue
            code = cand
            _EMAIL_CHANGE_CODES[cand] = {
                "old_email": old_email,
                "new_email": new_email,
                "expires": time.time() + _LOGIN_CODE_TTL_SEC,
            }
            break
    if not code:
        raise HTTPException(status_code=503, detail="Could not issue a code. Try again.")
    try:
        _send_email_change_code_email(to_email=new_email, code=code)
        logger.info("Email-change verification sent to %s", new_email)
    except RuntimeError as e:
        with _EMAIL_CHANGE_LOCK:
            _EMAIL_CHANGE_CODES.pop(code, None)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {
        "ok": True,
        "message": "Check the new inbox (and junk folder) for your 6-character verification code.",
    }


@app.post("/api/auth/confirm-email-change")
async def api_confirm_email_change(
    body: EmailChangeConfirmBody,
    request: Request,
    db: Session = Depends(get_db),
):
    viewer_uname = (_contributor_username_from_request(request) or "").strip()
    if not viewer_uname:
        raise HTTPException(status_code=401, detail="Sign in required")
    new_email = _normalize_login_email(body.new_email)
    code = body.code
    _purge_expired_email_change_codes()
    with _EMAIL_CHANGE_LOCK:
        rec = _EMAIL_CHANGE_CODES.get(code)
    if not rec or rec["expires"] < time.time():
        raise HTTPException(
            status_code=400,
            detail="That code is incorrect or has expired. Request a new code.",
        )
    if _normalize_login_email(rec.get("new_email") or "") != new_email:
        raise HTTPException(
            status_code=400,
            detail="That code is incorrect or has expired. Request a new code.",
        )
    old_from_token = _normalize_login_email(rec.get("old_email") or "")
    with _EMAIL_CHANGE_LOCK:
        _EMAIL_CHANGE_CODES.pop(code, None)
    db_user = _user_from_request(db, request)
    if not db_user or _normalize_login_email(db_user.email) != old_from_token:
        raise HTTPException(
            status_code=400,
            detail="Session mismatch. Close this window and try again.",
        )
    if db_user.username != viewer_uname:
        raise HTTPException(status_code=400, detail="Session mismatch. Try signing in again.")
    _migrate_user_to_new_email(db, db_user, new_email)
    return {"ok": True, "email": new_email, "username": viewer_uname}


@app.get("/sign-in/complete")
async def sign_in_complete(token: Optional[str] = None, db: Session = Depends(get_db)):
    """Legacy: consume a one-time token from an old email link; new sign-ins use a typed code."""
    raw = (token or "").strip()
    if not raw:
        return RedirectResponse(url="/?signin_msg=invalid_link", status_code=302)
    _purge_expired_sign_in_tokens()
    with _LOGIN_LOCK:
        rec = _LOGIN_TOKENS.get(raw)
    if not rec or rec["expires"] < time.time():
        return RedirectResponse(url="/?signin_msg=invalid_link", status_code=302)
    email = _normalize_login_email(rec.get("email") or "")
    user, _created = _ensure_user_for_magic_login(db, email)
    with _LOGIN_LOCK:
        _LOGIN_TOKENS.pop(raw, None)
    if not user.username_confirmed:
        r = RedirectResponse(url="/?finish_username=1", status_code=302)
        _issue_username_setup_cookie(r, email=email)
        return r
    username = user.username
    dest = "/"
    r = RedirectResponse(url=dest, status_code=302)
    r.set_cookie(
        CONTRIBUTOR_USERNAME_COOKIE,
        username,
        max_age=60 * 60 * 24 * 365,
        path="/",
        samesite="lax",
    )
    return r


@app.post("/api/contact")
async def api_contact_submit(
    category: str = Form(...),
    title: str = Form(...),
    message: str = Form(...),
):
    cat = (category or "").strip().lower()
    if cat not in CONTACT_CATEGORY_EMAILS:
        raise HTTPException(status_code=400, detail="Invalid category.")
    title_s = (title or "").strip()
    body_s = (message or "").strip()
    if not title_s:
        raise HTTPException(status_code=400, detail="Title is required.")
    if not body_s:
        raise HTTPException(status_code=400, detail="Message is required.")
    if len(title_s) > 200:
        raise HTTPException(status_code=400, detail="Title is too long.")
    if len(body_s) > 12000:
        raise HTTPException(status_code=400, detail="Message is too long.")

    label = CONTACT_CATEGORY_LABELS[cat]
    to_addr = CONTACT_CATEGORY_EMAILS[cat]
    email_subject = f"[{label}] {title_s}"
    email_body = (
        f"Category: {label}\n"
        f"Title: {title_s}\n\n"
        f"{body_s}\n"
    )

    try:
        _send_contact_smtp(to_email=to_addr, subject=email_subject, body_text=email_body)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception:
        logger.exception("Unexpected error sending contact email")
        raise HTTPException(
            status_code=502,
            detail="Failed to send your message. Please try again later.",
        )

    return JSONResponse({"ok": True})


@app.get("/privacy-policy")
async def privacy_policy_legacy_redirect():
    return RedirectResponse(url="/about/privacy-policy", status_code=301)


@app.get("/privacy")
async def privacy_legacy_redirect():
    return RedirectResponse(url="/about/privacy-policy", status_code=301)


@app.get("/affiliate-disclosure")
async def affiliate_disclosure_legacy_redirect():
    return RedirectResponse(url="/about/affiliate-disclosure", status_code=301)


@app.get("/faq")
async def faq_legacy_redirect():
    return RedirectResponse(url="/about/faq", status_code=301)


@app.get("/terms")
async def terms_page(request: Request):
    return templates.TemplateResponse(
        "terms.html",
        {"request": request, "nav_active": ""},
    )


def _admin_form_str(form: Any, key: str, default: str = "") -> str:
    v = form.get(key)
    if v is None:
        return default
    return str(v).strip()


def _validate_admin_http_url(s: str) -> str:
    """Validate a non-empty http(s) URL (max 2048 chars)."""
    sl = s.lower()
    if not (sl.startswith("http://") or sl.startswith("https://")):
        raise ValueError("Link must start with http:// or https://.")
    if len(s) > 2048:
        raise ValueError("Link is too long.")
    return s


def _admin_required_http_url_str(form: Any, key: str = "link") -> str:
    s = _admin_form_str(form, key)
    if not s:
        raise ValueError("Link URL is required.")
    return _validate_admin_http_url(s)


def _admin_form_uploads(form: Any, key: str) -> List[UploadFile]:
    try:
        raw = form.getlist(key)
    except Exception:
        return []
    return [v for v in raw if getattr(v, "filename", None)]


def _admin_page_response(request: Request, entity: str) -> Any:
    if entity not in ("products", "brands", "certifications"):
        entity = "products"
    authed = _admin_cookie_valid(request.cookies.get(ADMIN_SESSION_COOKIE))
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "nav_active": "",
            "admin_authenticated": authed,
            "admin_site_password_required": _admin_site_password_configured(),
            "admin_entity": entity,
        },
    )


@app.get("/admin")
async def admin_panel():
    return RedirectResponse(url="/admin/products", status_code=302)


@app.get("/admin/", include_in_schema=False)
async def admin_panel_trailing_slash():
    return RedirectResponse(url="/admin/products", status_code=307)


@app.get("/admin/products", include_in_schema=False)
async def admin_products_page(request: Request):
    return _admin_page_response(request, "products")


@app.get("/admin/products/", include_in_schema=False)
async def admin_products_page_slash():
    return RedirectResponse(url="/admin/products", status_code=307)


@app.get("/admin/brands", include_in_schema=False)
async def admin_brands_page(request: Request):
    return _admin_page_response(request, "brands")


@app.get("/admin/brands/", include_in_schema=False)
async def admin_brands_page_slash():
    return RedirectResponse(url="/admin/brands", status_code=307)


@app.get("/admin/certifications", include_in_schema=False)
async def admin_certifications_page(request: Request):
    return _admin_page_response(request, "certifications")


@app.get("/admin/certifications/", include_in_schema=False)
async def admin_certifications_page_slash():
    return RedirectResponse(url="/admin/certifications", status_code=307)


@app.get("/admin/tests", include_in_schema=False)
async def admin_self_tests_page(request: Request):
    authed = _admin_cookie_valid(request.cookies.get(ADMIN_SESSION_COOKIE))
    return templates.TemplateResponse(
        "admin_tests.html",
        {
            "request": request,
            "nav_active": "",
            "admin_authenticated": authed,
            "admin_site_password_required": _admin_site_password_configured(),
        },
    )


@app.get("/admin/tests/", include_in_schema=False)
async def admin_self_tests_page_slash():
    return RedirectResponse(url="/admin/tests", status_code=307)


@app.get("/admin/verify")
async def admin_verify_magic_link(token: str = ""):
    """Validate one-time token from email; with site password set, defer session until /admin/finish-signin."""
    raw = (token or "").strip()
    if not raw:
        return RedirectResponse(url="/admin/products", status_code=302)
    _purge_expired_admin_links()
    with _ADMIN_AUTH_LOCK:
        rec = _ADMIN_LINK_TOKENS.get(raw)
        if not rec or rec["expires"] < time.time():
            return RedirectResponse(url="/admin/products?msg=invalid_link", status_code=302)
        if not _admin_site_password_configured():
            del _ADMIN_LINK_TOKENS[raw]
    if _admin_site_password_configured():
        return RedirectResponse(
            url=f"/admin/finish-signin?token={quote(raw, safe='')}",
            status_code=302,
        )
    resp = RedirectResponse(url="/admin/products", status_code=302)
    _issue_admin_session_cookie(resp)
    return resp


@app.get("/admin/finish-signin")
async def admin_finish_signin_page(request: Request, token: str = ""):
    """Enter site password after opening the magic link (only when TABBED_ADMIN_SITE_PASSWORD is set)."""
    if not _admin_site_password_configured():
        return RedirectResponse(url="/admin/products", status_code=302)
    raw = (token or "").strip()
    if not raw:
        return RedirectResponse(url="/admin/products", status_code=302)
    _purge_expired_admin_links()
    with _ADMIN_AUTH_LOCK:
        rec = _ADMIN_LINK_TOKENS.get(raw)
        if not rec or rec["expires"] < time.time():
            return RedirectResponse(url="/admin/products?msg=invalid_link", status_code=302)
    return templates.TemplateResponse(
        "admin_finish_signin.html",
        {"request": request, "nav_active": "", "finish_token": raw},
    )


@app.get("/admin/finish-signin/", include_in_schema=False)
async def admin_finish_signin_page_slash():
    return RedirectResponse(url="/admin/finish-signin", status_code=307)


@app.post("/api/admin/verify-link")
async def admin_verify_link_finish(body: AdminFinishSignInBody):
    """Consume magic-link token and set session cookie after site password check."""
    if not _admin_site_password_configured():
        raise HTTPException(
            status_code=400,
            detail="Site password is not enabled for this server.",
        )
    if not _admin_site_password_matches(body.password):
        raise HTTPException(status_code=401, detail="Invalid site password.")
    raw = (body.token or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Missing token.")
    _purge_expired_admin_links()
    with _ADMIN_AUTH_LOCK:
        rec = _ADMIN_LINK_TOKENS.get(raw)
        if not rec or rec["expires"] < time.time():
            raise HTTPException(
                status_code=400,
                detail="That sign-in link is invalid or expired. Request a new one.",
            )
        del _ADMIN_LINK_TOKENS[raw]
    resp = JSONResponse({"ok": True})
    _issue_admin_session_cookie(resp)
    return resp


@app.post("/api/admin/send-link")
async def admin_send_sign_in_link(request: Request):
    """Email a one-time sign-in link to the fixed admin inbox only."""
    password = ""
    ct = (request.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        try:
            data = await request.json()
            if isinstance(data, dict) and data.get("password") is not None:
                password = str(data.get("password") or "")
        except Exception:
            pass
    if _admin_site_password_configured() and not _admin_site_password_matches(
        password
    ):
        raise HTTPException(status_code=401, detail="Invalid site password.")

    host = (os.environ.get("TABBED_SMTP_HOST") or "").strip()
    if not host:
        raise HTTPException(
            status_code=503,
            detail=(
                "Outgoing email is not configured. Set TABBED_SMTP_HOST (and credentials) "
                "in .env next to app.py."
            ),
        )
    _purge_expired_admin_links()
    link_token = secrets.token_urlsafe(32)
    with _ADMIN_AUTH_LOCK:
        _ADMIN_LINK_TOKENS.clear()
        _ADMIN_LINK_TOKENS[link_token] = {
            "expires": time.time() + _ADMIN_LINK_TTL_SEC,
        }
    mail_from = _admin_transactional_from_address()
    smtp_host = (os.environ.get("TABBED_SMTP_HOST") or "").strip()
    pub_base = _public_app_base_url()
    logger.info(
        "Admin sign-in email dispatch: to=%s from=%s smtp_host=%s link_base=%s",
        ADMIN_ALLOWED_EMAIL,
        mail_from,
        smtp_host or "(none)",
        pub_base,
    )
    if "127.0.0.1" in pub_base or "localhost" in pub_base.lower():
        logger.warning(
            "TABBED_PUBLIC_BASE_URL is %r — the link in the admin email only works on this "
            "machine. Set TABBED_PUBLIC_BASE_URL to your real HTTPS origin when testing from "
            "email on another device.",
            pub_base,
        )
    try:
        _send_admin_sign_in_link_email(token=link_token)
        logger.info("Admin sign-in SMTP send_message completed for to=%s", ADMIN_ALLOWED_EMAIL)
    except RuntimeError as e:
        with _ADMIN_AUTH_LOCK:
            _ADMIN_LINK_TOKENS.pop(link_token, None)
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("Admin sign-in email failed after token issued")
        with _ADMIN_AUTH_LOCK:
            _ADMIN_LINK_TOKENS.pop(link_token, None)
        raise HTTPException(
            status_code=503,
            detail="Could not send sign-in email. Check server logs and SMTP settings.",
        ) from e
    return {
        "ok": True,
        "message": "Sign-in link sent to admin email.",
    }


# Back-compat for older admin clients still calling POST /api/admin/send-code
@app.post("/api/admin/send-code", include_in_schema=False)
async def admin_send_sign_in_link_legacy(request: Request):
    return await admin_send_sign_in_link(request)


@app.post("/api/admin/logout")
async def admin_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    return resp


@app.post("/api/admin/run-tests", dependencies=[Depends(require_admin_session)])
async def admin_run_self_tests():
    """Run in-process security and integration checks (isolated in-memory DB; no real SMTP)."""
    from admin_self_tests import run_admin_self_tests

    return await run_admin_self_tests()


@app.post("/api/admin/brands", dependencies=[Depends(require_admin_session)])
async def admin_create_brand_row(request: Request, db: Session = Depends(get_db)):
    """Insert a row into ``brands`` (name + image BLOB) for catalog reference."""
    try:
        form = await request.form()
        name = _admin_form_str(form, "name")
        if not name:
            raise ValueError("Brand name is required.")
        link = _admin_required_http_url_str(form, "link")
        if db.query(Brand).filter(Brand.name == name).first():
            raise ValueError("A brand with this name already exists.")
        img = form.get("image")
        if img is None or not getattr(img, "filename", None):
            raise ValueError("Image is required.")
        blob = _normalize_brand_upload_to_blob(img, label=name)
        row = Brand(name=name, link=link, image=blob)
        db.add(row)
        db.commit()
        db.refresh(row)
        return {"success": True, "id": row.id, "name": row.name}
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/admin/certifications", dependencies=[Depends(require_admin_session)])
async def admin_create_certification_row(request: Request, db: Session = Depends(get_db)):
    """Insert a row into ``certifications`` (name + image BLOB)."""
    try:
        form = await request.form()
        name = _admin_form_str(form, "name")
        if not name:
            raise ValueError("Certification name is required.")
        link = _admin_required_http_url_str(form, "link")
        if db.query(Certification).filter(Certification.name == name).first():
            raise ValueError("A certification with this name already exists.")
        img = form.get("image")
        if img is None or not getattr(img, "filename", None):
            raise ValueError("Image is required.")
        blob = _normalize_upload_to_blob(img, label=name)
        row = Certification(name=name, link=link, image=blob)
        db.add(row)
        db.commit()
        db.refresh(row)
        return {"success": True, "id": row.id, "name": row.name}
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@app.post(
    "/api/admin/reference/vocab/made-with",
    dependencies=[Depends(require_admin_session)],
)
async def admin_vocab_add_made_with(
    body: AdminVocabNameBody, db: Session = Depends(get_db)
):
    try:
        n = _vocab_get_or_create_name(db, VocabMadeWith, body.name)
        db.commit()
        return {"ok": True, "name": n}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post(
    "/api/admin/reference/vocab/made-without",
    dependencies=[Depends(require_admin_session)],
)
async def admin_vocab_add_made_without(
    body: AdminVocabNameBody, db: Session = Depends(get_db)
):
    try:
        n = _vocab_get_or_create_name(db, VocabMadeWithout, body.name)
        db.commit()
        return {"ok": True, "name": n}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post(
    "/api/admin/reference/vocab/features",
    dependencies=[Depends(require_admin_session)],
)
async def admin_vocab_add_features(
    body: AdminVocabNameBody, db: Session = Depends(get_db)
):
    try:
        n = _vocab_get_or_create_name(db, VocabFeature, body.name)
        db.commit()
        return {"ok": True, "name": n}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.api_route(
    "/api/admin/brands/{brand_id}",
    methods=["PATCH", "PUT", "POST", "DELETE"],
    dependencies=[Depends(require_admin_session)],
)
async def admin_update_brand_row(
    brand_id: int, request: Request, db: Session = Depends(get_db)
):
    """Update brand display name, replace logo, or delete the row (DELETE)."""
    if request.method == "DELETE":
        row = db.query(Brand).filter(Brand.id == brand_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Brand not found.")
        in_use = (
            db.query(Product)
            .filter(Product.brand_id == brand_id)
            .count()
        )
        if in_use:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot delete: {in_use} product(s) use this brand. "
                    "Edit those products to another brand or remove them first."
                ),
            )
        try:
            db.delete(row)
            db.commit()
            return {"success": True, "id": brand_id}
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(e)) from e
    row = db.query(Brand).filter(Brand.id == brand_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Brand not found.")
    try:
        form = await request.form()
        name = _admin_form_str(form, "name")
        if not name:
            raise ValueError("Brand name is required.")
        link_raw = _admin_form_str(form, "link")
        img = form.get("image")
        has_new_image = bool(
            img is not None and getattr(img, "filename", None)
        )
        has_blob = row.image is not None and len(row.image) > 0
        if not has_blob and not has_new_image:
            raise ValueError("Image is required when this brand has no logo yet.")
        other = (
            db.query(Brand)
            .filter(Brand.name == name, Brand.id != brand_id)
            .first()
        )
        if other:
            raise ValueError("Another brand already uses this name.")
        row.name = name
        if link_raw:
            row.link = _validate_admin_http_url(link_raw)
        if has_new_image:
            row.image = _normalize_brand_upload_to_blob(img, label=name)
        db.commit()
        db.refresh(row)
        return {"success": True, "id": row.id, "name": row.name}
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from None


@app.api_route(
    "/api/admin/certifications/{certification_id}",
    methods=["PATCH", "PUT", "POST", "DELETE"],
    dependencies=[Depends(require_admin_session)],
)
async def admin_update_certification_row(
    certification_id: int, request: Request, db: Session = Depends(get_db)
):
    """Update certification, replace badge image, or delete the row (DELETE)."""
    if request.method == "DELETE":
        row = (
            db.query(Certification)
            .filter(Certification.id == certification_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Certification not found.")
        try:
            db.delete(row)
            db.commit()
            return {"success": True, "id": certification_id}
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(e)) from e
    row = (
        db.query(Certification).filter(Certification.id == certification_id).first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Certification not found.")
    try:
        form = await request.form()
        name = _admin_form_str(form, "name")
        if not name:
            raise ValueError("Certification name is required.")
        link_raw = _admin_form_str(form, "link")
        img = form.get("image")
        has_new_image = bool(
            img is not None and getattr(img, "filename", None)
        )
        has_blob = row.image is not None and len(row.image) > 0
        if not has_blob and not has_new_image:
            raise ValueError("Image is required when this certification has no image yet.")
        other = (
            db.query(Certification)
            .filter(Certification.name == name, Certification.id != certification_id)
            .first()
        )
        if other:
            raise ValueError("Another certification already uses this name.")
        row.name = name
        if link_raw:
            row.link = _validate_admin_http_url(link_raw)
        if has_new_image:
            row.image = _normalize_upload_to_blob(img, label=name)
        db.commit()
        db.refresh(row)
        return {"success": True, "id": row.id, "name": row.name}
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from None


def _blob_image_media_type(blob: bytes) -> str:
    if not blob or len(blob) < 12:
        return "application/octet-stream"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if blob[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "application/octet-stream"


@app.get("/api/admin/reference/brands", dependencies=[Depends(require_admin_session)])
async def admin_reference_brands_list(db: Session = Depends(get_db)):
    """Rows from ``brands`` for admin product pickers."""
    rows = db.query(Brand).order_by(Brand.name.asc()).all()
    return {
        "brands": [
            {
                "id": r.id,
                "name": r.name,
                "link": (getattr(r, "link", None) or "").strip(),
                "has_image": r.image is not None and len(r.image) > 0,
            }
            for r in rows
        ]
    }


@app.get("/api/admin/reference/brands/{brand_id}/image", dependencies=[Depends(require_admin_session)])
async def admin_reference_brand_image(brand_id: int, db: Session = Depends(get_db)):
    row = db.query(Brand).filter(Brand.id == brand_id).first()
    if not row or not row.image:
        raise HTTPException(status_code=404, detail="Image not found")
    mt = _blob_image_media_type(row.image)
    return Response(content=row.image, media_type=mt)


@app.get("/api/admin/reference/certifications", dependencies=[Depends(require_admin_session)])
async def admin_reference_certifications_list(db: Session = Depends(get_db)):
    """Rows from ``certifications`` for admin product pickers."""
    rows = db.query(Certification).order_by(Certification.name.asc()).all()
    return {
        "certifications": [
            {
                "id": r.id,
                "name": r.name,
                "link": (getattr(r, "link", None) or "").strip(),
                "has_image": r.image is not None and len(r.image) > 0,
            }
            for r in rows
        ]
    }


@app.get(
    "/api/admin/reference/certifications/{certification_id}/image",
    dependencies=[Depends(require_admin_session)],
)
async def admin_reference_certification_image(certification_id: int, db: Session = Depends(get_db)):
    row = db.query(Certification).filter(Certification.id == certification_id).first()
    if not row or not row.image:
        raise HTTPException(status_code=404, detail="Image not found")
    mt = _blob_image_media_type(row.image)
    return Response(content=row.image, media_type=mt)


@app.post("/api/admin/product-image-preview", dependencies=[Depends(require_admin_session)])
async def admin_product_image_preview(request: Request):
    """Return a base64 JPEG after the same normalization used when saving product images (admin form preview)."""
    try:
        form = await request.form()
        upload = form.get("product_image")
        if upload is None or not getattr(upload, "filename", None):
            raise ValueError("Choose an image file.")
        upload.file.seek(0)
        raw = upload.file.read()
        if not raw:
            raise ValueError("Empty file.")
        jpeg = _normalize_product_image_bytes(raw)
        if not jpeg:
            raise ValueError("Could not process that image.")
        return {
            "preview_base64": base64.b64encode(jpeg).decode("ascii"),
            "media_type": "image/jpeg",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:
        logger.exception("product-image-preview failed")
        raise HTTPException(status_code=400, detail="Could not process image.") from e


@app.post("/api/admin/products/ai-populate", dependencies=[Depends(require_admin_session)])
async def admin_product_ai_populate(body: AdminProductAiPopulateBody):
    """Scrape product URL and run the AI ingest pipeline; return fields for the add-product form."""
    try:
        from scripts.ai_product_ingest import run_ingest_for_form
    except Exception:
        logger.exception("AI ingest module import failed")
        raise HTTPException(
            status_code=500,
            detail="AI populate is not available (import error).",
        ) from None
    try:
        return run_ingest_for_form(body.url)
    except ValueError as e:
        logger.warning("ai-populate: %s", e)
        raise HTTPException(status_code=400, detail=str(e)) from None
    except RuntimeError as e:
        msg = str(e)
        logger.warning("ai-populate (RuntimeError): %s", msg)
        if "ANTHROPIC_API_KEY" in msg and "not set" in msg:
            raise HTTPException(status_code=503, detail=msg) from e
        if "anthropic" in msg.lower() and "not installed" in msg.lower():
            raise HTTPException(status_code=500, detail=msg) from e
        raise HTTPException(status_code=400, detail=msg) from e
    except Exception as e:
        logger.exception("admin_product_ai_populate failed for %s: %s", body.url, e)
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/admin/products/add", dependencies=[Depends(require_admin_session)])
async def admin_add_product(request: Request, db: Session = Depends(get_db)):
    """Admin: add a published catalog product."""
    try:
        form = await request.form()
        product_name = _admin_form_str(form, "product_name")
        brand_name = _admin_form_str(form, "brand_name")
        main_category = _admin_form_str(form, "main_category")
        subcategory = _admin_form_str(form, "subcategory")
        made_in = _admin_form_str(form, "made_in")
        if not product_name or not brand_name or not main_category or not made_in:
            raise ValueError("Product name, brand, main category, and Made In are required.")
        mc = _validate_main_category(main_category)
        sub = _validate_admin_subcategory_or_empty(db, mc, subcategory)
        made_with_list = json.loads(_admin_form_str(form, "made_with", "[]") or "[]")
        made_without_list = json.loads(_admin_form_str(form, "made_without", "[]") or "[]")
        attributes_list = json.loads(_admin_form_str(form, "attributes", "[]") or "[]")
        made_with_list, made_without_list, attributes_list = _normalize_product_tag_lists_to_vocab(
            db, made_with_list, made_without_list, attributes_list
        )
        certs_meta = json.loads(_admin_form_str(form, "certifications", "[]") or "[]")
        if not isinstance(certs_meta, list):
            certs_meta = []
        cert_files = _admin_form_uploads(form, "cert_images")
        price_raw = _admin_form_str(form, "price")
        price_val = 0.0
        if price_raw:
            price_val = float(price_raw)
        link = _admin_form_str(form, "product_link") or None
        description = _admin_form_str(form, "description") or None
        earns = _parse_form_bool(form.get("earns_commission"))
        verified = _parse_form_bool(form.get("is_verified"))

        product_image = form.get("product_image")
        brand_image = form.get("brand_image")
        product_image_data = None
        product_image_filename = None
        if product_image is not None and getattr(product_image, "filename", None):
            product_image_data, product_image_filename = _save_normalized_upload(
                upload=product_image, prefix="product", label=product_name
            )

        brand_row = _admin_resolve_brand(db, brand_name, brand_image)
        product = _add_product(
            db,
            product_name=product_name,
            brand_row=brand_row,
            main_category=mc,
            subcategory=sub,
            made_in=made_in,
            price=price_val,
            product_link=link,
            earns_commission=earns,
            made_with_list=made_with_list,
            made_without_list=made_without_list,
            attributes_list=attributes_list,
            description=description,
            product_image_data=product_image_data,
            product_image_filename=product_image_filename,
            is_verified=verified,
        )
        _admin_apply_product_certifications(db, product, certs_meta, cert_files)
        db.commit()
        db.refresh(product)
        return {
            "success": True,
            "message": "Product added",
            "product": _product_api_dict(product, db),
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/admin/products", dependencies=[Depends(require_admin_session)])
async def admin_list_products(db: Session = Depends(get_db)):
    """Admin: all catalog products for spreadsheet / bulk tools (same JSON shape as ``/api/products`` items)."""
    products = (
        db.query(Product)
        .options(joinedload(Product.brand), selectinload(Product.certifications))
        .order_by(Product.id.asc())
        .all()
    )
    return {"products": [_product_api_dict(p, db) for p in products]}


@app.get("/api/admin/products/{product_id}", dependencies=[Depends(require_admin_session)])
async def admin_get_product(product_id: int, db: Session = Depends(get_db)):
    """Admin: one catalog product (same JSON shape as items in ``/api/products``)."""
    product = (
        db.query(Product)
        .options(joinedload(Product.brand), selectinload(Product.certifications))
        .filter(Product.id == product_id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return _product_api_dict(product, db)


@app.post("/api/admin/products/{product_id}", dependencies=[Depends(require_admin_session)])
async def admin_update_product(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Admin: update a published catalog product."""
    product = (
        db.query(Product)
        .options(joinedload(Product.brand), selectinload(Product.certifications))
        .filter(Product.id == product_id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    try:
        form = await request.form()
        product_name = _admin_form_str(form, "product_name")
        brand_name = _admin_form_str(form, "brand_name")
        main_category = _admin_form_str(form, "main_category")
        subcategory = _admin_form_str(form, "subcategory")
        made_in = _admin_form_str(form, "made_in")
        if not product_name or not brand_name or not main_category or not made_in:
            raise ValueError("Product name, brand, main category, and Made In are required.")

        made_with_list = json.loads(_admin_form_str(form, "made_with", "[]") or "[]")
        made_without_list = json.loads(_admin_form_str(form, "made_without", "[]") or "[]")
        attributes_list = json.loads(_admin_form_str(form, "attributes", "[]") or "[]")
        made_with_list, made_without_list, attributes_list = _normalize_product_tag_lists_to_vocab(
            db, made_with_list, made_without_list, attributes_list
        )
        certs_meta = json.loads(_admin_form_str(form, "certifications", "[]") or "[]")
        if not isinstance(certs_meta, list):
            certs_meta = []
        cert_files = _admin_form_uploads(form, "cert_images")

        brand_image = form.get("brand_image")
        brand_row = _admin_resolve_brand(db, brand_name, brand_image)

        product.product_name = product_name
        product.brand_id = brand_row.id
        mc = _validate_main_category(main_category)
        product.main_category = mc
        product.subcategory = _validate_admin_subcategory_or_empty(db, mc, subcategory)
        product.made_in = made_in
        price_raw = _admin_form_str(form, "price")
        if price_raw != "":
            product.price = float(price_raw)
        link = _admin_form_str(form, "product_link") or None
        product.product_link = link
        product.earns_commission = _parse_form_bool(form.get("earns_commission"))
        product.is_verified = _parse_form_bool(form.get("is_verified"))
        product.made_with = made_with_list
        product.made_without = made_without_list
        product.attributes = attributes_list
        product.description = _admin_form_str(form, "description") or None

        _admin_apply_product_certifications(db, product, certs_meta, cert_files)

        product_image = form.get("product_image")
        if product_image is not None and getattr(product_image, "filename", None):
            product_image_data, product_image_filename = _save_normalized_upload(
                upload=product_image, prefix="product", label=product_name
            )
            product.product_image = product_image_data
            product.product_image_filename = product_image_filename

        db.commit()
        db.refresh(product)
        return {
            "success": True,
            "message": "Product updated",
            "product": _product_api_dict(product, db),
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@app.post(
    "/api/admin/products/bulk-delete",
    dependencies=[Depends(require_admin_session)],
)
async def admin_bulk_delete_products(
    body: AdminBulkDeleteProductsBody, db: Session = Depends(get_db)
):
    """Admin: remove one or more catalog products (favorites and cert links cascade). Uses POST so proxies that block DELETE still work."""
    found = (
        db.query(Product)
        .options(joinedload(Product.brand))
        .filter(Product.id.in_(body.ids))
        .all()
    )
    by_id = {p.id: p for p in found}
    deleted: list[dict] = []
    not_found: list[int] = []
    for pid in body.ids:
        product = by_id.get(pid)
        if not product:
            not_found.append(pid)
            continue
        name = (product.product_name or "").strip() or "(no name)"
        brand_name = (product.brand.name if product.brand else "") or ""
        brand_name = (brand_name or "").strip()
        deleted.append(
            {
                "id": pid,
                "name": name,
                "brand_name": brand_name,
            }
        )
        db.delete(product)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    n = len(deleted)
    if n:
        message = f"Deleted {n} product(s)."
    elif not_found:
        message = f"No products deleted; {len(not_found)} id(s) not found."
    else:
        message = "No products deleted."
    return {
        "ok": True,
        "deleted": deleted,
        "not_found": not_found,
        "message": message,
    }


@app.delete(
    "/api/admin/products/{product_id}",
    dependencies=[Depends(require_admin_session)],
)
async def admin_delete_product(product_id: int, db: Session = Depends(get_db)):
    """Admin: remove a catalog product; favorites and product–certification links cascade."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    name = (product.product_name or "").strip() or "(no name)"
    try:
        db.delete(product)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "ok": True,
        "message": f"Product #{product_id} ({name}) was deleted",
        "id": product_id,
    }


@app.get("/api/articles")
async def get_published_articles():
    """Articles feed disabled; catalog is DB-managed."""
    return {"articles": []}


@app.get("/api/products/search")
async def get_products_search_catalog(q: str = "", db: Session = Depends(get_db)):
    """Search the catalog: products + facets (shop UI; not category-scoped). ``q`` must be non-empty."""
    payload = _search_catalog_payload(db, q)
    if not payload:
        raise HTTPException(status_code=400, detail="Missing or empty search query")
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@app.get("/api/products/all")
async def get_products_all_catalog(db: Session = Depends(get_db)):
    """Full catalog for the shop: products + facets (same JSON shape as ``/api/products/category/{slug}``)."""
    payload = _all_catalog_payload(db)
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@app.get("/api/products/category/{slug}")
async def get_products_for_category(slug: str, db: Session = Depends(get_db)):
    """One category shelf: products + facets (slug, alternate casing, or main display name in path)."""
    payload = _category_catalog_payload(db, slug)
    if not payload:
        raise HTTPException(status_code=404, detail="Unknown category")
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@app.get("/api/products")
async def get_products_all(db: Session = Depends(get_db)):
    """Full published product list (``products`` key only; no ``facets``)."""
    return _all_products_list_response(db)


@app.get("/api/categories")
async def get_categories():
    """Canonical categories for navigation and admin — in-memory snapshot from startup (same as nav)."""
    body = {"categories": _categories_nav_cached()}
    return JSONResponse(
        content=body,
        headers={"Cache-Control": "no-store, must-revalidate, Pragma: no-cache"},
    )


@app.get("/api/brands")
async def get_brands(db: Session = Depends(get_db)):
    """Get unique brand names from products (via ``brands`` table)."""
    rows = (
        db.query(Brand.name)
        .join(Product, Product.brand_id == Brand.id)
        .distinct()
        .order_by(Brand.name)
        .all()
    )
    return {"brands": [r[0] for r in rows if r[0]]}

@app.get("/api/made_in")
async def get_made_in(db: Session = Depends(get_db)):
    """Get unique countries from products."""
    countries = db.query(Product.made_in).distinct().all()
    return {"made_in": [c[0] for c in countries if c[0]]}


@app.get("/api/brands/{brand_id}/image")
async def api_public_brand_image(brand_id: int, db: Session = Depends(get_db)):
    row = db.query(Brand).filter(Brand.id == brand_id).first()
    if not row or not row.image:
        raise HTTPException(status_code=404, detail="Not found")
    mt = _blob_image_media_type(row.image)
    return Response(content=row.image, media_type=mt)


@app.get("/api/certifications/{certification_id}/image")
async def api_public_certification_image(
    certification_id: int, db: Session = Depends(get_db)
):
    row = (
        db.query(Certification)
        .filter(Certification.id == certification_id)
        .first()
    )
    if not row or not row.image:
        raise HTTPException(status_code=404, detail="Not found")
    mt = _blob_image_media_type(row.image)
    return Response(content=row.image, media_type=mt)


def _distinct_certification_catalog(db: Session) -> List[dict]:
    """Certification catalog rows (``certifications`` table) for admin / picker."""
    rows = db.query(Certification).order_by(Certification.name.asc()).all()
    out: List[dict] = []
    for r in rows:
        img_url = f"/api/certifications/{r.id}/image" if r.image else None
        clink = (getattr(r, "link", None) or "").strip()
        out.append(
            {
                "id": r.id,
                "certification_name": r.name,
                "name": r.name,
                "certification_image_filename": None,
                "image_filename": None,
                "image_url": img_url,
                "link": clink or None,
                "certification_link": clink or None,
            }
        )
    return out


def _distinct_attribute_tags(db: Session) -> Tuple[List[str], List[str], List[str]]:
    """Canonical made_with / made_without / feature labels (vocabulary tables)."""
    made_with = [r[0] for r in db.query(VocabMadeWith.name).order_by(VocabMadeWith.name.asc()).all()]
    made_without = [
        r[0] for r in db.query(VocabMadeWithout.name).order_by(VocabMadeWithout.name.asc()).all()
    ]
    features = [r[0] for r in db.query(VocabFeature.name).order_by(VocabFeature.name.asc()).all()]
    return made_with, made_without, features


@app.get("/api/certifications")
async def get_certifications(db: Session = Depends(get_db)):
    """Distinct certifications from products (admin picker)."""
    return {"certifications": _distinct_certification_catalog(db)}


@app.get("/api/product-attribute-tags")
async def get_product_attribute_tags(db: Session = Depends(get_db)):
    """Distinct made_with / made_without / attributes from catalog."""
    made_with, made_without, attributes = _distinct_attribute_tags(db)
    return {"made_with": made_with, "made_without": made_without, "attributes": attributes}


def _products_matching_search_query(db: Session, q: str) -> List[Product]:
    raw = (q or "").strip()
    if not raw:
        return []
    like = f"%{raw}%"
    cert_pid_subq = (
        select(product_certifications.c.product_id)
        .join(Certification, Certification.id == product_certifications.c.certification_id)
        .where(Certification.name.ilike(like))
    )
    return (
        db.query(Product)
        .options(joinedload(Product.brand), selectinload(Product.certifications))
        .outerjoin(Brand, Product.brand_id == Brand.id)
        .filter(
            or_(
                Product.product_name.ilike(like),
                Brand.name.ilike(like),
                Product.main_category.ilike(like),
                Product.subcategory.ilike(like),
                func.coalesce(Product.description, "").ilike(like),
                Product.id.in_(cert_pid_subq),
            )
        )
        .distinct()
        .all()
    )


def _search_results_payload(products: List[Product]) -> List[dict]:
    results = []
    for product in products:
        b = getattr(product, "brand", None)
        results.append(
            {
                "id": product.id,
                "name": product.product_name,
                "brand_name": b.name if b else "",
                "main_category": product.main_category,
                "subcategory": _normalize_subcategory(getattr(product, "subcategory", None)),
                "category": product.main_category,
            }
        )
    return results


@app.get("/search")
async def search_page(request: Request, q: str = "", db: Session = Depends(get_db)):
    qstrip = (q or "").strip()
    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "nav_active": "",
            "search_query": qstrip,
            "show_site_affiliate_strip": bool(qstrip),
        },
    )


@app.get("/all")
async def all_categories_shop_page(request: Request):
    """Browse every product in the catalog (shop layout; data from ``/api/products/all``)."""
    return templates.TemplateResponse(
        "category_shop.html",
        {
            "request": request,
            "nav_active": "home",
            "category": {"slug": "all", "name": "All categories"},
        },
    )


@app.get("/all/", include_in_schema=False)
async def all_categories_shop_page_trailing_slash():
    """Canonical `/all` (avoid duplicate shop pages when links use a trailing slash)."""
    return RedirectResponse(url="/all", status_code=307)


@app.get("/api/search")
async def search(q: str = "", db: Session = Depends(get_db)):
    """Search products by name or brand."""
    if not (q or "").strip():
        return {"results": [], "query": q or ""}
    products = _products_matching_search_query(db, q)
    return {"results": _search_results_payload(products), "query": (q or "").strip()}


@app.get("/api/favorites")
async def api_get_my_favorites(request: Request, db: Session = Depends(get_db)):
    user = _user_from_request(db, request)
    if not user:
        return {"ids": []}
    rows = (
        db.query(UserFavorite.product_id)
        .filter(UserFavorite.user_email == user.email)
        .order_by(UserFavorite.id.asc())
        .all()
    )
    return {"ids": [int(r[0]) for r in rows]}


@app.get("/api/users/{username}/avatar")
async def api_get_user_avatar(username: str, db: Session = Depends(get_db)):
    uname = (username or "").strip()
    if not uname:
        return RedirectResponse(url="/static/person.svg", status_code=302)
    row = db.query(User).filter(User.username == uname).first()
    if (
        row
        and getattr(row, "avatar_image", None)
        and getattr(row, "avatar_uploaded_at", None)
    ):
        mime = (row.avatar_mime_type or "image/jpeg").strip() or "image/jpeg"
        if mime not in ("image/jpeg", "image/png", "image/webp"):
            mime = "image/jpeg"
        return Response(content=row.avatar_image, media_type=mime)
    return RedirectResponse(url="/static/person.svg", status_code=302)


@app.post("/api/me/avatar")
async def api_post_my_avatar(
    request: Request,
    avatar: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = _user_from_request(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required")
    raw = await avatar.read()
    if len(raw) > 4 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 4MB).")
    try:
        jpeg_bytes, mime = _normalize_uploaded_avatar(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    user.avatar_image = jpeg_bytes
    user.avatar_mime_type = mime
    user.avatar_uploaded_at = datetime.utcnow()
    db.add(user)
    try:
        db.commit()
    except OperationalError as e:
        db.rollback()
        logger.exception("Avatar DB commit failed: %s", e)
        msg = (
            "Could not save your photo: database write failed. "
            "Check your database is reachable and writable (TABBED_DATABASE_URL for PostgreSQL; "
            "file permissions and TABBED_SQLITE_PATH for local SQLite)."
        )
        raise HTTPException(status_code=503, detail=msg) from e
    return {"ok": True}


@app.delete("/api/me/avatar")
async def api_delete_my_avatar(request: Request, db: Session = Depends(get_db)):
    user = _user_from_request(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required")
    user.avatar_image = None
    user.avatar_mime_type = None
    user.avatar_uploaded_at = None
    db.add(user)
    try:
        db.commit()
    except OperationalError as e:
        db.rollback()
        logger.exception("Avatar delete DB commit failed: %s", e)
        msg = (
            "Could not remove your photo: database write failed. "
            "Check your database is reachable and writable (TABBED_DATABASE_URL for PostgreSQL; "
            "file permissions and TABBED_SQLITE_PATH for local SQLite)."
        )
        raise HTTPException(status_code=503, detail=msg) from e
    return {"ok": True}


@app.get("/api/users/{username}/favorites")
async def api_get_user_favorite_ids(
    username: str,
    request: Request,
    db: Session = Depends(get_db),
):
    owner = db.query(User).filter(User.username == username).first()
    if not owner:
        return {"ids": []}
    viewer = (_contributor_username_from_request(request) or "").strip()
    merged = _merged_profile_settings(owner)
    if viewer != username and merged.get("favorites-visible") != "yes":
        return {"ids": []}
    rows = (
        db.query(UserFavorite.product_id)
        .filter(UserFavorite.user_email == owner.email)
        .order_by(UserFavorite.id.asc())
        .all()
    )
    return {"ids": [int(r[0]) for r in rows]}


@app.post("/api/favorites")
async def api_write_favorite(
    body: FavoriteWriteBody,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _user_from_request(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required")
    if not db.query(Product.id).filter(Product.id == body.product_id).first():
        raise HTTPException(status_code=404, detail="Product not found")
    row = (
        db.query(UserFavorite)
        .filter(
            UserFavorite.user_email == user.email,
            UserFavorite.product_id == body.product_id,
        )
        .first()
    )
    try:
        if body.favorited:
            if not row:
                db.add(UserFavorite(user_email=user.email, product_id=body.product_id))
        else:
            if row:
                db.delete(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Could not update favorite") from None
    return {"ok": True}


@app.get("/api/settings")
async def get_settings(
    request: Request,
    username: Optional[str] = None,
    db: Session = Depends(get_db),
):
    username = (username or "").strip() or _contributor_username_from_request(request)
    if not username:
        return {"settings": None}
    viewer = _user_from_request(db, request)
    if not viewer or viewer.username != username:
        return {"settings": None}
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return {"settings": None}
    return {"settings": _merged_profile_settings(user)}


@app.post("/api/settings")
async def save_settings(
    request: Request,
    username: Optional[str] = None,
    db: Session = Depends(get_db),
):
    username = (username or "").strip() or _contributor_username_from_request(request)
    if not username:
        raise HTTPException(status_code=400, detail="Username required")
    actor = _user_from_request(db, request)
    if not actor or actor.username != username:
        raise HTTPException(status_code=403, detail="Not allowed")
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON")
    merged = _merged_profile_settings(user)
    for k in _DEFAULT_PROFILE_SETTINGS:
        if k not in body:
            continue
        s = str(body[k]).strip().lower()
        if s not in ("yes", "no"):
            raise HTTPException(status_code=400, detail=f"Invalid value for {k}")
        merged[k] = s
    # Explicit UPDATE: assigning JSON on the ORM instance can skip emitting UPDATE when
    # SQLAlchemy compares the new dict as equal to the loaded value (JSON round-trip).
    try:
        db.execute(
            update(User)
            .where(User.email == user.email)
            .values(profile_settings=merged)
        )
        db.commit()
    except OperationalError as e:
        db.rollback()
        logger.exception("Settings DB commit failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Could not save settings. Check database permissions.",
        ) from e
    return {"ok": True, "settings": merged}


@app.get("/api/payouts")
async def get_payouts(request: Request, username: Optional[str] = None):
    if not ((username or "").strip() or _contributor_username_from_request(request)):
        return {"payouts": []}
    return {"payouts": []}


@app.get("/user/{username}/card")
async def user_profile_card(request: Request, username: str, db: Session = Depends(get_db)):
    kwargs = _profile_hero_template_kwargs(db, username, request)
    return templates.TemplateResponse(
        "profile_card.html",
        {"request": request, **kwargs},
    )


@app.get("/logout")
async def logout():
    r = RedirectResponse(url="/", status_code=302)
    r.delete_cookie(CONTRIBUTOR_USERNAME_COOKIE, path="/")
    _clear_username_setup_cookie(r)
    return r


@app.get("/user/{username}/settings")
async def user_profile_settings(request: Request, username: str, db: Session = Depends(get_db)):
    kwargs = _profile_hero_template_kwargs(db, username, request)
    return templates.TemplateResponse(
        "user_settings.html",
        {"request": request, "nav_active": "", "account_tab": "settings", **kwargs},
    )


@app.get("/user/{username}/payout-history")
async def user_profile_payout_history(request: Request, username: str, db: Session = Depends(get_db)):
    kwargs = _profile_hero_template_kwargs(db, username, request)
    return templates.TemplateResponse(
        "user_payout_history.html",
        {"request": request, "nav_active": "", "account_tab": "payout", **kwargs},
    )


@app.get("/user/{username}")
async def user_profile(request: Request, username: str, db: Session = Depends(get_db)):
    kwargs = _profile_hero_template_kwargs(db, username, request)
    return templates.TemplateResponse(
        "username.html",
        {"request": request, "nav_active": "", "account_tab": "favorites", **kwargs},
    )


@app.get("/{main_slug}/{sub_slug}")
async def category_shop_page_nested(
    request: Request,
    main_slug: str,
    sub_slug: str,
    db: Session = Depends(get_db),
):
    """Subcategory shop: ``/main_slug/sub_slug`` (sub segment matches DB child slug after ``main-`` prefix)."""
    if main_slug in _CATEGORY_SHOP_RESERVED_MAIN_SLUGS:
        raise HTTPException(status_code=404, detail="Not found")
    cat = _category_for_nested_shop_path(db, main_slug, sub_slug)
    if not cat:
        raise HTTPException(status_code=404, detail="Not found")
    canonical = _category_sub_url_segment(main_slug, cat.slug)
    if sub_slug != canonical:
        return RedirectResponse(
            url=f"/{quote(main_slug, safe='')}/{quote(canonical, safe='')}",
            status_code=307,
        )
    return templates.TemplateResponse(
        "category_shop.html",
        {
            "request": request,
            "nav_active": "home",
            "category": {"slug": cat.slug, "name": cat.name},
        },
    )


@app.get("/{category_slug}")
async def category_shop_page(
    request: Request,
    category_slug: str,
    db: Session = Depends(get_db),
):
    """Main shelf or legacy single-segment child URL; child rows redirect to ``/parent/segment``."""
    if (category_slug or "").strip().lower() == "all":
        return RedirectResponse(url="/all", status_code=307)
    cat = _category_for_shop_path(db, category_slug)
    if not cat:
        raise HTTPException(status_code=404, detail="Not found")
    if cat.parent_id is not None:
        parent = db.query(Category).filter(Category.id == cat.parent_id).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Not found")
        seg = _category_sub_url_segment(parent.slug, cat.slug)
        return RedirectResponse(
            url=f"/{quote(parent.slug, safe='')}/{quote(seg, safe='')}",
            status_code=301,
        )
    if category_slug != cat.slug:
        return RedirectResponse(url=f"/{quote(cat.slug, safe='')}", status_code=307)
    return templates.TemplateResponse(
        "category_shop.html",
        {
            "request": request,
            "nav_active": "home",
            "category": {"slug": cat.slug, "name": cat.name},
        },
    )

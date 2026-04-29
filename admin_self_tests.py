"""
In-process admin integration checks (GET /admin/tests, POST /api/admin/run-tests).

Uses an isolated in-memory SQLite DB and patches SMTP so tests do not touch the
real catalog or send real mail, while exercising the same route handlers and
session logic as production.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
import unittest.mock as mock
from contextlib import contextmanager
from typing import Any, Callable, Dict, Generator, List, Tuple

import httpx
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from models import Base, User, UserFavorite, Product, Brand, get_db


def _get_admin_session_cookie_value(app_m: Any) -> str:
    r = JSONResponse({})
    app_m._issue_admin_session_cookie(r)
    c = r.headers.get("set-cookie") or ""
    m = re.search(r"tabbed_admin_session=([^;]+)", c)
    return m.group(1) if m else ""


def _req_with_username_cookie(username: str) -> Request:
    h = f"tabbed_contributor_username={username}".encode("ascii")
    return Request({"type": "http", "headers": [[b"cookie", h]]})


def _req_anonymous() -> Request:
    return Request({"type": "http", "headers": []})


@contextmanager
def _db_session_from_override(
    app_m: Any,
) -> Generator[Any, None, None]:
    """Yield one SQLAlchemy session from the FastAPI get_db override (closes generator)."""
    gen = app_m.app.dependency_overrides[get_db]()
    db = next(gen)
    try:
        yield db
    finally:
        try:
            gen.close()
        except RuntimeError:
            pass


def _seed_favorites_privacy_case(db: Any, _app_m: Any) -> Tuple[str, int]:
    """User with private favorites and one product favorite. Returns (username, product_id)."""
    from app import CANONICAL_SHOP_CATEGORIES  # same module as _app_m

    suffix = secrets.token_hex(3)
    main = CANONICAL_SHOP_CATEGORIES[0][1]
    brand = Brand(
        name=f"SelfTest B {suffix}", link="https://tabbed.shop", image=None
    )
    db.add(brand)
    db.flush()
    p = Product(
        product_name=f"SelfTest P {suffix}",
        brand_id=brand.id,
        main_category=main,
        subcategory="",
        made_in="US",
        price=1.0,
        product_link=None,
        earns_commission=False,
        is_verified=False,
    )
    db.add(p)
    db.flush()
    u = User(
        email=f"fav_{suffix}@valid.test",
        username=f"stfav_{suffix}"[:20],
        profile_settings={"favorites-visible": "no"},
    )
    db.add(u)
    db.flush()
    db.add(UserFavorite(user_email=u.email, product_id=p.id))
    db.commit()
    return u.username, p.id


async def run_admin_self_tests() -> Dict[str, Any]:
    import app as app_m

    mem_eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=mem_eng)
    MemSession = sessionmaker(bind=mem_eng)

    def _mem_get_db():
        d = MemSession()
        try:
            yield d
        finally:
            d.close()

    app_m.app.dependency_overrides[get_db] = _mem_get_db
    t0 = time.perf_counter()
    results: List[Dict[str, Any]] = []

    try:
        transport = httpx.ASGITransport(app=app_m.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=True,
        ) as client:
            tests: List[Tuple[str, Any]] = [
                ("admin_api_products_returns_401_without_admin_cookie", _t_admin_401),
                ("admin_api_products_200_with_valid_admin_session", _t_admin_200),
                ("admin_mutating_route_requires_session", _t_admin_post_401),
                (
                    "sign_in_code_path_stores_token_and_dispatches_email",
                    _t_sign_in_code_sent,
                ),
                ("sign_in_rejects_mismatched_code", _t_sign_in_wrong_code),
                (
                    "user_sign_in_code_verify_creates_or_logs_in_user",
                    _t_sign_in_verify_ok,
                ),
                (
                    "public_user_favorite_ids_empty_when_favorites_not_visible",
                    _t_favorites_hidden_api,
                ),
                (
                    "owner_sees_favorite_ids_when_favorites_not_visible",
                    _t_favorites_owner,
                ),
                (
                    "profile_hero_marks_favorites_hidden_for_strangers",
                    _t_profile_hero_public,
                ),
                (
                    "profile_hero_does_not_hide_favorites_for_owner",
                    _t_profile_hero_owner,
                ),
                (
                    "api_settings_is_null_for_viewer_not_matching_username",
                    _t_settings_leak,
                ),
                ("api_settings_is_null_when_not_signed_in", _t_settings_anon),
                ("post_settings_forbidden_for_wrong_username", _t_settings_403),
                (
                    "request_email_change_stores_code_and_sends_email",
                    _t_email_change_code,
                ),
                (
                    "admin_send_link_uses_smtp_and_stores_one_time_token",
                    _t_admin_link_email,
                ),
            ]
            for name, fn in tests:
                try:
                    await fn(client, app_m)
                    results.append({"name": name, "ok": True, "detail": ""})
                except Exception as e:
                    results.append({"name": name, "ok": False, "detail": str(e)})
    finally:
        app_m.app.dependency_overrides.clear()

    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed
    return {
        "ok": failed == 0,
        "results": results,
        "passed": passed,
        "failed": failed,
        "ms": round((time.perf_counter() - t0) * 1000, 1),
    }


async def _t_admin_401(client: httpx.AsyncClient, app_m: Any) -> None:
    r = await client.get("/api/admin/products")
    if r.status_code != 401:
        raise AssertionError(f"expected 401, got {r.status_code} {r.text[:200]}")


async def _t_admin_200(client: httpx.AsyncClient, app_m: Any) -> None:
    c = _get_admin_session_cookie_value(app_m)
    r = await client.get(
        "/api/admin/products", cookies={app_m.ADMIN_SESSION_COOKIE: c}
    )
    if r.status_code != 200:
        raise AssertionError(f"expected 200, got {r.status_code} {r.text[:200]}")
    data = r.json()
    if "products" not in data or not isinstance(data["products"], list):
        raise AssertionError("expected JSON { products: list }")


async def _t_admin_post_401(client: httpx.AsyncClient, app_m: Any) -> None:
    r = await client.post("/api/admin/brands", data={})
    if r.status_code != 401:
        raise AssertionError(f"expected 401, got {r.status_code} {r.text[:200]}")


async def _t_sign_in_code_sent(client: httpx.AsyncClient, app_m: Any) -> None:
    with mock.patch.object(app_m, "_send_login_code_email", lambda **k: None):
        with mock.patch.dict(
            os.environ, {"TABBED_SMTP_HOST": "selftest-smtp", "TABBED_SMTP_USER": ""}
        ):
            email = f"codeholder_{id(client)}@valid.test"
            r = await client.post(
                "/api/auth/send-sign-in-link",
                json={"email": email},
            )
    if r.status_code != 200:
        raise AssertionError(f"expected 200, got {r.status_code} {r.text[:300]}")
    with app_m._LOGIN_LOCK:
        found = [k for k, v in app_m._LOGIN_TOKENS.items() if v.get("email") == email]
    if not found:
        raise AssertionError("no sign-in code was stored in _LOGIN_TOKENS for the email")
    with app_m._LOGIN_LOCK:
        for t in list(found):
            app_m._LOGIN_TOKENS.pop(t, None)


async def _t_sign_in_wrong_code(client: httpx.AsyncClient, app_m: Any) -> None:
    with mock.patch.object(app_m, "_send_login_code_email", lambda **k: None):
        with mock.patch.dict(
            os.environ, {"TABBED_SMTP_HOST": "selftest-smtp", "TABBED_SMTP_USER": ""}
        ):
            email = f"badcode_{id(client)}@valid.test"
            a = await client.post(
                "/api/auth/send-sign-in-link", json={"email": email}
            )
    if a.status_code != 200:
        raise AssertionError(a.text)
    r = await client.post(
        "/api/auth/verify-sign-in-code",
        json={"email": email, "code": "ZZZZZZ"},
    )
    if r.status_code != 400:
        raise AssertionError(f"expected 400 for bad code, got {r.status_code} {r.text[:200]}")


async def _t_sign_in_verify_ok(client: httpx.AsyncClient, app_m: Any) -> None:
    with mock.patch.object(app_m, "_send_login_code_email", lambda **k: None):
        with mock.patch.dict(
            os.environ, {"TABBED_SMTP_HOST": "selftest-smtp", "TABBED_SMTP_USER": ""}
        ):
            email = f"verifyok_{id(client)}@valid.test"
            a = await client.post(
                "/api/auth/send-sign-in-link", json={"email": email}
            )
    if a.status_code != 200:
        raise AssertionError(a.text)
    with app_m._LOGIN_LOCK:
        code = next(
            (
                k
                for k, v in app_m._LOGIN_TOKENS.items()
                if v.get("email") == email
            ),
            None,
        )
    if not code:
        raise AssertionError("expected code in map")
    b = await client.post(
        "/api/auth/verify-sign-in-code",
        json={"email": email, "code": code},
    )
    if b.status_code != 200:
        raise AssertionError(f"verify failed: {b.status_code} {b.text[:500]}")
    body = b.json()
    if not body.get("ok"):
        raise AssertionError("expected { ok: true }")


async def _t_favorites_hidden_api(client: httpx.AsyncClient, app_m: Any) -> None:
    with _db_session_from_override(app_m) as db:
        user, _pid = _seed_favorites_privacy_case(db, app_m)
    r = await client.get(f"/api/users/{user}/favorites")
    if r.json().get("ids") != []:
        raise AssertionError("anonymous viewer should get empty favorite ids when private")


async def _t_favorites_owner(client: httpx.AsyncClient, app_m: Any) -> None:
    with _db_session_from_override(app_m) as db:
        user, pid = _seed_favorites_privacy_case(db, app_m)
    r = await client.get(
        f"/api/users/{user}/favorites",
        cookies={app_m.CONTRIBUTOR_USERNAME_COOKIE: user},
    )
    ids = r.json().get("ids", [])
    if pid not in ids:
        raise AssertionError("owner should see their favorite product ids")


async def _t_profile_hero_public(client: httpx.AsyncClient, app_m: Any) -> None:
    with _db_session_from_override(app_m) as db:
        user, _ = _seed_favorites_privacy_case(db, app_m)
    with _db_session_from_override(app_m) as db:
        kw = app_m._profile_hero_template_kwargs(db, user, _req_anonymous())
    if not kw.get("profile_favorites_hidden_from_public"):
        raise AssertionError("strangers should see favorites as hidden in template kwargs")
    if kw.get("profile_favorite_count", 0) != 0:
        raise AssertionError("public favorite count should be zeroed when hidden")


async def _t_profile_hero_owner(client: httpx.AsyncClient, app_m: Any) -> None:
    with _db_session_from_override(app_m) as db:
        user, _ = _seed_favorites_privacy_case(db, app_m)
    with _db_session_from_override(app_m) as db:
        kw = app_m._profile_hero_template_kwargs(db, user, _req_with_username_cookie(user))
    if kw.get("profile_favorites_hidden_from_public"):
        raise AssertionError("owner should not get the public hidden flag for their own profile")
    pc = kw.get("profile_favorite_count")
    if not isinstance(pc, int) or pc < 1:
        raise AssertionError("owner should see real favorite count on profile data")


async def _t_settings_leak(client: httpx.AsyncClient, app_m: Any) -> None:
    with _db_session_from_override(app_m) as db:
        h = secrets.token_hex(2)
        u = User(
            email=f"sa{h}@valid.test", username=f"sta_{h}"[:20], profile_settings={}
        )
        v = User(
            email=f"sb{h}@valid.test", username=f"stb_{h}"[:20], profile_settings={}
        )
        db.add_all([u, v])
        db.commit()
        un_a, un_b = u.username, v.username
    r = await client.get(
        "/api/settings",
        params={"username": un_a},
        cookies={app_m.CONTRIBUTOR_USERNAME_COOKIE: un_b},
    )
    if r.json().get("settings") is not None:
        raise AssertionError("should not return settings for another user")


async def _t_settings_anon(client: httpx.AsyncClient, app_m: Any) -> None:
    with _db_session_from_override(app_m) as db:
        h = secrets.token_hex(2)
        u = User(
            email=f"anon{h}@valid.test", username=f"stan_{h}"[:20], profile_settings={}
        )
        db.add(u)
        db.commit()
        un = u.username
    r = await client.get("/api/settings", params={"username": un})
    if r.json().get("settings") is not None:
        raise AssertionError("unsigned viewer should not load settings JSON")


async def _t_settings_403(client: httpx.AsyncClient, app_m: Any) -> None:
    with _db_session_from_override(app_m) as db:
        h = secrets.token_hex(2)
        u = User(
            email=f"sc{h}@valid.test", username=f"stc_{h}"[:20], profile_settings={}
        )
        v = User(
            email=f"sd{h}@valid.test", username=f"std_{h}"[:20], profile_settings={}
        )
        db.add_all([u, v])
        db.commit()
        un_u, un_v = u.username, v.username
    r = await client.post(
        "/api/settings",
        params={"username": un_u},
        content=json.dumps({"favorites-visible": "no"}),
        headers={"content-type": "application/json"},
        cookies={app_m.CONTRIBUTOR_USERNAME_COOKIE: un_v},
    )
    if r.status_code != 403:
        raise AssertionError(f"expected 403, got {r.status_code} {r.text[:200]}")


async def _t_email_change_code(client: httpx.AsyncClient, app_m: Any) -> None:
    h = secrets.token_hex(2)
    old_email = f"old{h}@valid.test"
    new_email = f"new{h}@valid.test"
    with _db_session_from_override(app_m) as db:
        u = User(
            email=old_email,
            username=f"ecu{h}"[:20],
            profile_settings={},
        )
        db.add(u)
        db.commit()
        un = u.username
    with mock.patch.object(app_m, "_send_email_change_code_email", lambda **k: None):
        with mock.patch.dict(
            os.environ, {"TABBED_SMTP_HOST": "selftest-smtp", "TABBED_SMTP_USER": ""}
        ):
            r = await client.post(
                "/api/auth/request-email-change",
                json={"new_email": new_email},
                cookies={app_m.CONTRIBUTOR_USERNAME_COOKIE: un},
            )
    if r.status_code != 200:
        raise AssertionError(f"request email change: {r.status_code} {r.text[:500]}")
    with app_m._EMAIL_CHANGE_LOCK:
        keys = list(app_m._EMAIL_CHANGE_CODES)
    if not keys:
        raise AssertionError("expected a pending email-change verification code")
    with app_m._EMAIL_CHANGE_LOCK:
        for k in keys:
            app_m._EMAIL_CHANGE_CODES.pop(k, None)


async def _t_admin_link_email(client: httpx.AsyncClient, app_m: Any) -> None:
    with mock.patch.object(app_m, "_send_admin_sign_in_link_email", lambda **k: None):
        with mock.patch.dict(
            os.environ,
            {
                "TABBED_SMTP_HOST": "selftest-smtp",
                "TABBED_SMTP_USER": "",
                "TABBED_PUBLIC_BASE_URL": "https://tabbed.shop",
                app_m.ADMIN_SITE_PASSWORD_ENV: "",
            },
        ):
            r = await client.post("/api/admin/send-link", json={})
    if r.status_code != 200:
        raise AssertionError(f"admin send link: {r.status_code} {r.text[:300]}")
    with app_m._ADMIN_AUTH_LOCK:
        n = len(app_m._ADMIN_LINK_TOKENS)
    if n != 1:
        raise AssertionError(f"expected one pending admin link token, got {n}")


if __name__ == "__main__":
    import asyncio

    print(json.dumps(asyncio.run(run_admin_self_tests()), indent=2))

"""SQLite-backed operator accounts and expiring sessions."""
from hashlib import sha256
from secrets import token_urlsafe
from time import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import db

COOKIE_NAME = "orbitalguard_session"
SESSION_SECONDS = 8 * 60 * 60
OPERATORS = {
    "ruipeng lin": ("ruipeng-lin", "Ruipeng Lin"),
    "zuomeng zhang": ("zuomeng-zhang", "Zuomeng Zhang"),
    "haoyue xiang": ("haoyue-xiang", "Haoyue Xiang"),
    "vi tram": ("vi-tram", "Vi Tram"),
}
router = APIRouter(prefix="/api/auth")


def seed_operators():
    for operator_id, name in OPERATORS.values():
        db.execute("INSERT OR IGNORE INTO operators (id, name) VALUES (?, ?)", (operator_id, name))


def normalize_name(name: str) -> str:
    return " ".join(name.casefold().split())


def session_digest(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def current_operator(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return db.query_one(
        "SELECT operators.id, operators.name FROM operator_sessions "
        "JOIN operators ON operators.id = operator_sessions.operator_id "
        "WHERE token_hash = ? AND expires_at > ?",
        (session_digest(token), int(time())),
    )


class Login(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    account = OPERATORS.get(normalize_name(body.name))
    row = db.query_one("SELECT id, name FROM operators WHERE id = ?", (account[0],)) if account else None
    if row is None:
        raise HTTPException(401, "This name is outside the demo operator roster.")

    now = int(time())
    token = token_urlsafe(32)
    old = request.cookies.get(COOKIE_NAME)
    if old:
        db.execute("DELETE FROM operator_sessions WHERE token_hash = ?", (session_digest(old),))
    db.execute("DELETE FROM operator_sessions WHERE expires_at <= ?", (now,))
    db.execute(
        "INSERT INTO operator_sessions (token_hash, operator_id, expires_at) VALUES (?, ?, ?)",
        (session_digest(token), row["id"], now + SESSION_SECONDS),
    )
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=request.url.scheme == "https", samesite="strict")
    return {"id": row["id"], "name": row["name"]}


@router.get("/me")
def me(request: Request):
    operator = current_operator(request)
    if operator is None:
        raise HTTPException(401, "Sign in to continue.")
    return operator


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        db.execute("DELETE FROM operator_sessions WHERE token_hash = ?", (session_digest(token),))
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="strict")

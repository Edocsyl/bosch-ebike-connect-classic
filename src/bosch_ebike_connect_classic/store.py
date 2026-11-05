"""SQLite store: OAuth clients/codes/tokens + AES-256-GCM sealed Bosch sessions.

- Bearer/refresh tokens and auth codes are stored only as SHA-256 hashes.
- The Bosch session (REMEMBER cookie) is sealed with AES-256-GCM; the row key
  (account_key) is bound as AAD so a blob cannot be swapped between accounts.
- The Bosch password is never stored.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .logutil import log, mask_email

ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
CODE_TTL = 600

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients(
  client_id TEXT PRIMARY KEY, name TEXT, redirect_uris TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS codes(
  code_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, account_key TEXT NOT NULL,
  redirect_uri TEXT NOT NULL, challenge TEXT NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tokens(
  token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, client_id TEXT NOT NULL,
  account_key TEXT NOT NULL, expires_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS tokens_account ON tokens(account_key);
CREATE TABLE IF NOT EXISTS accounts(
  account_key TEXT PRIMARY KEY, blob TEXT NOT NULL, updated_at INTEGER NOT NULL);
"""


def h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_account_key(email: str) -> str:
    return email.strip().lower()


class Store:
    def __init__(self, path: Path, secret: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        old_umask = os.umask(0o077)  # db, -wal and -shm are created owner-only from the start
        try:
            self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
        finally:
            os.umask(old_umask)
        for suffix in ("", "-wal", "-shm"):
            try:
                os.chmod(f"{path}{suffix}", 0o600)
            except OSError:
                pass
        self._lock = threading.Lock()
        key = HKDF(hashes.SHA256(), 32, None, b"bosch-ebike-connect/blob/v1").derive(secret.encode())
        self._aes = AESGCM(key)

    # --- sealed Bosch session ------------------------------------------------
    def _seal(self, account_key: str, data: dict) -> str:
        nonce = os.urandom(12)
        ct = self._aes.encrypt(nonce, json.dumps(data).encode(), account_key.encode())
        return base64.b64encode(b"v1" + nonce + ct).decode()

    def _open(self, account_key: str, blob: str) -> dict:
        raw = base64.b64decode(blob)
        if raw[:2] != b"v1":
            raise ValueError("unknown blob version")
        return json.loads(self._aes.decrypt(raw[2:14], raw[14:], account_key.encode()))

    def upsert_account(self, account_key: str, data: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO accounts VALUES(?,?,?) ON CONFLICT(account_key) DO UPDATE "
                "SET blob=excluded.blob, updated_at=excluded.updated_at",
                (account_key, self._seal(account_key, data), int(time.time())),
            )

    def get_account(self, account_key: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT blob FROM accounts WHERE account_key=?", (account_key,)
            ).fetchone()
        return self._open(account_key, row[0]) if row else None

    def update_remember(self, account_key: str, remember: str) -> None:
        acc = self.get_account(account_key)
        if acc is not None and acc.get("remember") != remember:
            self.upsert_account(account_key, {**acc, "remember": remember})

    def delete_account(self, account_key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM accounts WHERE account_key=?", (account_key,))
            self._db.execute("DELETE FROM tokens WHERE account_key=?", (account_key,))
            self._db.execute("DELETE FROM codes WHERE account_key=?", (account_key,))

    # --- OAuth clients ---------------------------------------------------------
    def create_client(self, name: str, redirect_uris: list[str]) -> str:
        client_id = uuid.uuid4().hex
        with self._lock:
            self._db.execute(
                "INSERT INTO clients VALUES(?,?,?,?)",
                (client_id, name[:100], json.dumps(redirect_uris), int(time.time())),
            )
        return client_id

    def count_clients(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM clients").fetchone()[0]

    def get_client(self, client_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT client_id,name,redirect_uris FROM clients WHERE client_id=?", (client_id,)
            ).fetchone()
        return {"client_id": row[0], "name": row[1], "redirect_uris": json.loads(row[2])} if row else None

    # --- codes & tokens ----------------------------------------------------------
    def put_code(self, client_id: str, account_key: str, redirect_uri: str, challenge: str) -> str:
        code = secrets.token_urlsafe(32)
        with self._lock:
            self._db.execute(
                "INSERT INTO codes VALUES(?,?,?,?,?,?)",
                (h(code), client_id, account_key, redirect_uri, challenge, int(time.time()) + CODE_TTL),
            )
        return code

    def pop_code(self, code: str) -> dict | None:
        """One-time use: the row is deleted whether or not the caller's checks pass."""
        with self._lock:
            row = self._db.execute(
                "DELETE FROM codes WHERE code_hash=? RETURNING client_id,account_key,"
                "redirect_uri,challenge,expires_at", (h(code),)
            ).fetchone()
        if not row or row[4] < time.time():
            return None
        return dict(zip(("client_id", "account_key", "redirect_uri", "challenge"), row[:4]))

    def issue_tokens(self, client_id: str, account_key: str) -> dict:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        with self._lock:
            self._db.execute("INSERT INTO tokens VALUES(?,?,?,?,?)",
                             (h(access), "access", client_id, account_key, now + ACCESS_TTL))
            self._db.execute("INSERT INTO tokens VALUES(?,?,?,?,?)",
                             (h(refresh), "refresh", client_id, account_key, now + REFRESH_TTL))
        return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL,
                "refresh_token": refresh}

    def rotate_refresh(self, refresh: str, client_id: str) -> dict | None:
        """Rotate a refresh token. The spent token stays as 'refresh_used' until it expires:
        presenting it again means it leaked, so every token of that client/account is revoked."""
        with self._lock:
            row = self._db.execute(
                "UPDATE tokens SET kind='refresh_used' WHERE token_hash=? AND kind='refresh' "
                "AND client_id=? RETURNING account_key,expires_at", (h(refresh), client_id)
            ).fetchone()
            if not row:
                reused = self._db.execute(
                    "SELECT account_key FROM tokens WHERE token_hash=? AND kind='refresh_used' "
                    "AND client_id=?", (h(refresh), client_id)
                ).fetchone()
                if reused:
                    self._db.execute("DELETE FROM tokens WHERE account_key=? AND client_id=?",
                                     (reused[0], client_id))
                    log.warning("oauth: spent refresh token replayed, revoked all tokens of %s for this client",
                                mask_email(reused[0]))
                return None
        if row[1] < time.time():
            return None
        return self.issue_tokens(client_id, row[0])

    def lookup_access(self, token: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT account_key FROM tokens WHERE token_hash=? AND kind='access' AND expires_at>?",
                (h(token), int(time.time())),
            ).fetchone()
        return row[0] if row else None

    def revoke_tokens(self, account_key: str) -> None:
        """Forces the client through the OAuth flow again (used when Bosch session expired)."""
        with self._lock:
            self._db.execute("DELETE FROM tokens WHERE account_key=?", (account_key,))

    def purge_expired(self) -> None:
        now = int(time.time())
        with self._lock:
            self._db.execute("DELETE FROM tokens WHERE expires_at<?", (now,))
            self._db.execute("DELETE FROM codes WHERE expires_at<?", (now,))
            # clients nobody ever finished a sign-in with (open registration must not grow forever)
            self._db.execute(
                "DELETE FROM clients WHERE created_at<? AND client_id NOT IN "
                "(SELECT client_id FROM tokens UNION SELECT client_id FROM codes)", (now - 7 * 86400,))

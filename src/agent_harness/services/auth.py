"""One trusted-host configured local account; opaque tokens are stored only as hashes."""

import hashlib
import re
import secrets
from collections import deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from ..models.auth import AuthSession, AuthStatus
from ..models.chat import CurrentUser
from .database import ChatError


class AuthService:
    def __init__(self, database, owner):
        self.database = database
        self.owner = owner
        self.hasher = PasswordHasher(memory_cost=65536, time_cost=3, parallelism=1)
        self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))
        self._attempts = deque(maxlen=10)
        self._lock = Lock()

    def _rate_limit(self):
        with self._lock:
            now = monotonic()
            while self._attempts and self._attempts[0] <= now - 60:
                self._attempts.popleft()
            if len(self._attempts) >= 10:
                raise ChatError("authentication_rate_limited", 429)
            self._attempts.append(now)

    def status(self):
        with self.database.connect() as connection:
            account = connection.execute("SELECT user_id FROM agent_chat.local_account").fetchone()
        return AuthStatus(setup_required=account is None)

    def _session(self, connection):
        token = "session_" + secrets.token_urlsafe(32)
        expiry = datetime.now(timezone.utc) + timedelta(hours=8)
        connection.execute(
            "DELETE FROM agent_chat.auth_sessions "
            "WHERE expires_at <= now() OR revoked_at IS NOT NULL"
        )
        connection.execute(
            "INSERT INTO agent_chat.auth_sessions(token_hash,user_id,expires_at) VALUES (%s,%s,%s)",
            (hashlib.sha256(token.encode()).hexdigest(), self.owner, expiry),
        )
        user = connection.execute(
            "SELECT id,email,created_at FROM agent_chat.users WHERE id=%s", (self.owner,)
        ).fetchone()
        return AuthSession(session_token=token, expires_at=expiry, user=CurrentUser(**user))

    def setup(self, body):
        self._rate_limit()
        password_hash = self.hasher.hash(body.password.get_secret_value())
        with self.database.connect() as connection:
            # Serialize first setup before any email or account change.
            connection.execute("SELECT pg_advisory_xact_lock(71304922)")
            if connection.execute("SELECT user_id FROM agent_chat.local_account").fetchone():
                raise ChatError("account_already_configured", 409)
            connection.execute(
                "UPDATE agent_chat.users SET email=%s WHERE id=%s", (body.email, self.owner)
            )
            connection.execute(
                "INSERT INTO agent_chat.local_account(user_id,password_hash) VALUES (%s,%s)",
                (self.owner, password_hash),
            )
            return self._session(connection)

    def login(self, body):
        self._rate_limit()
        with self.database.connect() as connection:
            account = connection.execute(
                "SELECT a.password_hash,u.email FROM agent_chat.local_account a "
                "JOIN agent_chat.users u ON u.id=a.user_id WHERE a.user_id=%s",
                (self.owner,),
            ).fetchone()
        try:
            valid = self.hasher.verify(
                account["password_hash"] if account else self._dummy_hash,
                body.password.get_secret_value(),
            )
        except VerificationError:
            valid = False
        if not account or not valid or not secrets.compare_digest(account["email"], body.email):
            raise ChatError("invalid_credentials", 401)
        with self.database.connect() as connection:
            return self._session(connection)

    def valid_session(self, token):
        if not re.fullmatch(r"session_[A-Za-z0-9_-]{43}", token):
            return False
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM agent_chat.auth_sessions s "
                "JOIN agent_chat.local_account a ON a.user_id=s.user_id "
                "WHERE s.token_hash=%s AND s.user_id=%s "
                "AND s.expires_at>now() AND s.revoked_at IS NULL",
                (hashlib.sha256(token.encode()).hexdigest(), self.owner),
            ).fetchone()
        return row is not None

    def logout(self, token):
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE agent_chat.auth_sessions SET revoked_at=now() "
                "WHERE token_hash=%s AND user_id=%s",
                (hashlib.sha256(token.encode()).hexdigest(), self.owner),
            )

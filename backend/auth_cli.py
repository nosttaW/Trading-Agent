"""Generate an ADMIN_PASSWORD_HASH without storing plaintext."""
from __future__ import annotations

import getpass
import hashlib
import secrets
import sys
import base64


def hash_password(password: str, iterations: int = 600_000) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(derived).decode()}"


if __name__ == "__main__":
    if sys.argv[1:] != ["hash-password"]:
        raise SystemExit("usage: python -m backend.auth_cli hash-password")
    first = getpass.getpass("Admin password: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise SystemExit("passwords do not match")
    if len(first) < 14:
        raise SystemExit("password must contain at least 14 characters")
    print(hash_password(first))

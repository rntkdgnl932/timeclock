# timeclock/auth.py
# -*- coding: utf-8 -*-
import os
import hmac
import base64
import hashlib
from typing import Optional


def pbkdf2_hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """
    저장 포맷: pbkdf2_sha256$iterations$salt_b64$hash_b64
    """
    if salt is None:
        salt = os.urandom(16)
    iterations = 200_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def pbkdf2_verify_password(password: str, stored: str) -> bool:
    try:
        algo, it_s, salt_b64, hash_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(it_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False

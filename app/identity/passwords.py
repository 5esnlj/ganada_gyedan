from functools import cache
from hashlib import scrypt
from secrets import compare_digest, token_bytes


def _digest(password: str, salt: bytes) -> bytes:
    return scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)


def hash_password(password: str) -> str:
    salt = token_bytes(16)
    return "$".join(("scrypt-v1", salt.hex(), _digest(password, salt).hex()))


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = encoded.split("$")
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
    except ValueError:
        return False
    if scheme != "scrypt-v1" or len(salt) != 16 or len(expected) != 32:
        return False
    return compare_digest(_digest(password, salt), expected)


@cache
def demo_password_hash() -> str:
    """Share a hash only for the documented, intentionally shared fixture password."""
    return hash_password("mock-pass-123")

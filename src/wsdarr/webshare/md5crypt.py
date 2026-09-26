"""Pure-python MD5-crypt (``$1$``) as used by Webshare's login password hashing.

Python 3.13 removed the ``crypt`` module, so we ship the (well known, public domain) algorithm
by Poul-Henning Kamp ourselves instead of depending on an unmaintained library.
"""

from __future__ import annotations

import hashlib

MAGIC = "$1$"
ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _to64(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(ITOA64[value & 0x3F])
        value >>= 6
    return "".join(out)


def md5crypt(password: str | bytes, salt: str, magic: str = MAGIC) -> str:
    """Return the full md5-crypt string ``$1$<salt>$<hash>``."""
    pw = password.encode("utf-8") if isinstance(password, str) else password
    if salt.startswith(magic):
        salt = salt[len(magic) :]
    salt = salt.split("$", 1)[0][:8]
    salt_b = salt.encode("utf-8")
    magic_b = magic.encode("utf-8")

    ctx = hashlib.md5(pw + magic_b + salt_b)
    final = hashlib.md5(pw + salt_b + pw).digest()

    for pl in range(len(pw), 0, -16):
        ctx.update(final[: min(16, pl)])

    i = len(pw)
    while i:
        ctx.update(b"\x00" if i & 1 else pw[:1])
        i >>= 1

    final = ctx.digest()

    for i in range(1000):
        ctx1 = hashlib.md5()
        ctx1.update(pw if i & 1 else final)
        if i % 3:
            ctx1.update(salt_b)
        if i % 7:
            ctx1.update(pw)
        ctx1.update(final if i & 1 else pw)
        final = ctx1.digest()

    f = final
    encoded = (
        _to64((f[0] << 16) | (f[6] << 8) | f[12], 4)
        + _to64((f[1] << 16) | (f[7] << 8) | f[13], 4)
        + _to64((f[2] << 16) | (f[8] << 8) | f[14], 4)
        + _to64((f[3] << 16) | (f[9] << 8) | f[15], 4)
        + _to64((f[4] << 16) | (f[10] << 8) | f[5], 4)
        + _to64(f[11], 2)
    )
    return f"{magic}{salt}${encoded}"


def webshare_password_hash(password: str, salt: str) -> str:
    """Webshare ``password`` login parameter: ``SHA1(MD5_CRYPT(password, salt))``."""
    return hashlib.sha1(md5crypt(password, salt).encode("utf-8")).hexdigest()


def webshare_digest(username: str, password: str) -> str:
    """Webshare ``digest`` login parameter: ``MD5(username + ':Webshare:' + password)``."""
    return hashlib.md5(f"{username}:Webshare:{password}".encode()).hexdigest()

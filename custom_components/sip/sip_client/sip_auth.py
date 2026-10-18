"""RFC 2617 HTTP Digest authentication (MD5)."""

from __future__ import annotations

import hashlib


def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def digest_response(
    username: str,
    password: str,
    realm: str,
    method: str,
    uri: str,
    nonce: str,
    qop: str,
    nc: str,
    cnonce: str,
) -> str:
    """Compute the Digest ``response`` value.

    When ``qop`` is non-empty the qop=auth variant (with nc/cnonce) is used,
    otherwise the legacy RFC 2069 form.
    """
    ha1 = md5_hex(f"{username}:{realm}:{password}")
    ha2 = md5_hex(f"{method}:{uri}")
    if qop:
        return md5_hex(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return md5_hex(f"{ha1}:{nonce}:{ha2}")

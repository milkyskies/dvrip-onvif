"""WS-Security UsernameToken checking.

Frigate's ONVIF client signs every request with a UsernameToken. By default it
sends a password digest, which is Base64(SHA1(nonce + created + password)), and
some clients send the password as text instead. This module accepts both and
compares in constant time.

The credentials here guard the shim. They are not the camera login.
"""

import base64
import hashlib
import hmac

from soap import WSSE, WSU, find_text, qname

PASSWORD_DIGEST = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
)


def _token(header):
    if header is None:
        return None

    security = header.find(qname(WSSE, "Security"))
    if security is None:
        return None

    return security.find(qname(WSSE, "UsernameToken"))


def is_authorised(header, username: str, password: str) -> bool:
    """Return True when the header carries a UsernameToken that matches."""
    token = _token(header)
    if token is None:
        return False

    if not hmac.compare_digest(find_text(token, WSSE, "Username"), username):
        return False

    password_element = token.find(qname(WSSE, "Password"))
    if password_element is None or password_element.text is None:
        return False

    supplied = password_element.text
    if password_element.get("Type", PASSWORD_DIGEST) != PASSWORD_DIGEST:
        # A plain text password.
        return hmac.compare_digest(supplied, password)

    nonce = find_text(token, WSSE, "Nonce")
    created = find_text(token, WSU, "Created")
    if not created:
        return False

    try:
        nonce_bytes = base64.b64decode(nonce, validate=True)
    except (ValueError, TypeError):
        return False

    digest = hashlib.sha1(
        nonce_bytes + created.encode("utf-8") + password.encode("utf-8")
    ).digest()

    return hmac.compare_digest(base64.b64encode(digest).decode("ascii"), supplied)

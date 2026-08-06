import base64
import binascii


def decode_base64url(value: str) -> bytes:
    try:
        padding = "=" * ((4 - len(value) % 4) % 4)
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Subscription key is not valid base64url") from exc


def validate_subscription_keys(p256dh: str, auth: str) -> None:
    public_key = decode_base64url(p256dh)
    auth_secret = decode_base64url(auth)
    if len(public_key) != 65 or public_key[0] != 4:
        raise ValueError("p256dh must be an uncompressed P-256 public key")
    if len(auth_secret) != 16:
        raise ValueError("auth must be a 16-byte secret")

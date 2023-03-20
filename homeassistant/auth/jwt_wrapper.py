"""Provide a wrapper around JWT that caches decoding tokens.

Since we decode the same tokens over and over again
we can cache the result of the decode of valid tokens
to speed up the process.
"""
from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from typing import Any

from jwt import DecodeError, PyJWS, PyJWT

from homeassistant.util.json import json_loads


class _PyJWSWithLoadCache(PyJWS):
    """PyJWS with a dedicated load implementation."""

    @lru_cache(maxsize=16)
    # We only ever have a global instance of this class
    # so we do not have to worry about the LRU growing
    # each time we create a new instance.
    def _load(self, jwt: str | bytes) -> tuple[bytes, bytes, dict, bytes]:
        """Load a JWS."""
        return super()._load(jwt)


_jws = _PyJWSWithLoadCache()


class _PyJWTWithVerify(PyJWT):
    """PyJWT with a dedicated verify implementation."""

    def decode_payload(
        self,
        jwt: str,
        options: dict[str, Any],
        algorithms: list[str],
        key: str | None = None,
    ) -> dict[str, Any]:
        """Decode a JWT's payload."""
        try:
            payload = json_loads(
                _jws.decode_complete(
                    jwt=jwt,
                    key=key or "",
                    algorithms=algorithms,
                    options=options,
                )["payload"]
            )
        except ValueError as err:
            raise DecodeError(f"Invalid payload string: {err}") from err
        if not isinstance(payload, dict):
            raise DecodeError("Invalid payload string: must be a json object")
        return payload

    def verify(
        self,
        jwt: str,
        key: str,
        algorithms: list[str],
        issuer: str | None,
        leeway: int | float | timedelta,
    ) -> None:
        """Verify a JWT's signature and claims."""
        options = {"verify_signature": True}
        self._validate_claims(  # type: ignore[no-untyped-call]
            payload=self.decode_payload(
                jwt=jwt,
                key=key,
                options=options,
                algorithms=algorithms,
            ),
            options={**self.options, **options},
            issuer=issuer,
            leeway=leeway,
        )


_jwt = _PyJWTWithVerify()  # type: ignore[no-untyped-call]
verify = _jwt.verify
decode_payload = _jwt.decode_payload

__all__ = [
    "decode_payload",
    "verify",
]

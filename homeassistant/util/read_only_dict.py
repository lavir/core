"""Read only dictionary."""
from typing import Any, TypeVar


def _readonly(*args: Any, **kwargs: Any) -> Any:
    """Raise an exception when a read only dict is modified."""
    raise RuntimeError("Cannot modify ReadOnlyDict")


_KT = TypeVar("_KT")
_VT = TypeVar("_VT")


class ReadOnlyDict(dict[_KT, _VT]):
    """Read only version of dict that is compatible with dict types."""

    def __hash__(self) -> int:  # type: ignore[override]
        """Return the hash of the dict."""
        return id(self)

    def __eq__(self, other: object) -> bool:
        """Return if the dict is equal to another object."""
        return id(self) == id(other)

    __setitem__ = _readonly
    __delitem__ = _readonly
    pop = _readonly
    popitem = _readonly
    clear = _readonly
    update = _readonly
    setdefault = _readonly

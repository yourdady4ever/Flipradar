"""Service-layer exceptions.  The API maps them to HTTP status codes (see ``flipradar.main``).

* :class:`NotFound`  -> 404 (an id/key in the URL path does not exist)
* :class:`Conflict`  -> 409 (the request conflicts with the current state)
* :class:`Invalid`   -> 422 (semantically invalid input that passed schema validation)
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base class; ``str(exc)`` is a safe, human-readable message."""


class NotFound(ServiceError, LookupError):
    pass


class Conflict(ServiceError):
    pass


class Invalid(ServiceError, ValueError):
    pass

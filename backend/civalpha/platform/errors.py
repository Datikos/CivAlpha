"""Exceptions with an HTTP meaning, and the problem type that job logs show verbatim.

NotFound -> 404 and BadRequest -> 400 (body {"error": message}). Problem is an expected, user-actionable failure
(missing prices, rate limits, a misconfiguration): a job that raises it logs "FAILED: <message>".
"""
from __future__ import annotations


class NotFound(Exception):
    pass


class BadRequest(Exception):
    pass


class Unavailable(Exception):
    pass


class Problem(Exception):
    pass


class Unauthorized(Exception):
    """401: no caller, or the sign-in failed (ADR-0005)."""


class Forbidden(Exception):
    """403: a caller without the role the route needs."""


class TooManyRequests(Exception):
    """429: the sign-in limit for one address was reached."""

"""Bounded errors that are safe to return to an API client."""


class LeashByteError(Exception):
    """An expected error with a public message."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

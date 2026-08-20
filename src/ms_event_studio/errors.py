"""Typed public failures used at project and UI boundaries."""

from __future__ import annotations


class MSEventStudioError(Exception):
    """Base class for expected, user-actionable failures."""


class MSParseError(MSEventStudioError):
    """The source cannot be parsed without guessing or discarding evidence."""


class InputChangedError(MSEventStudioError):
    """The source changed while it was being read."""


class CancelledError(MSEventStudioError):
    """A long-running operation was explicitly cancelled."""


class PathSecurityError(MSEventStudioError):
    """A manifest path is absolute, ambiguous, or escapes the project root."""


class ReviewConflict(MSEventStudioError):
    """The review row was changed after the caller last read it."""


class SnapError(MSEventStudioError):
    """A requested manual point cannot be snapped under the scientific rule."""


class ExistingEventNavigation(MSEventStudioError):
    """The requested Add resolves to evidence already owned by one event."""

    def __init__(self, event_id: str):
        self.event_id = str(event_id)
        super().__init__(f"snapped evidence is already owned by event {self.event_id}")


class ProjectValidationError(MSEventStudioError):
    """A project is incomplete, inconsistent, or has changed on disk."""


class WorkspaceRequestError(ValueError):
    """A browser request that cannot be mapped to a safe scientific action."""

    def __init__(self, message: str, *, code: str = "invalid_workspace_request") -> None:
        super().__init__(message)
        self.code = code

class AEOPError(Exception):
    pass


class RetryableError(AEOPError):
    """Transient failure: safe to retry with backoff."""
    def __init__(self, msg, retry_after=None):
        super().__init__(msg)
        self.retry_after = retry_after


class PermanentError(AEOPError):
    """Will not succeed on retry (bad input, 4xx, validation)."""


class AmbiguousOutcome(AEOPError):
    """External side effect may or may not have happened. Never blindly retry."""


class PermissionDenied(AEOPError):
    pass


class BudgetExceeded(AEOPError):
    pass


class InvalidTransition(AEOPError):
    pass


class NotFound(AEOPError):
    pass


class Conflict(AEOPError):
    pass


class NeedsReview(AEOPError):
    """Raised by an agent to route the run to manual review."""

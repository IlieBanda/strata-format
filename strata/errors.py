"""Exception hierarchy for Strata."""


class StrataError(Exception):
    """Base class for all Strata errors."""


class CorruptArchive(StrataError):
    """Raised when the container structure is damaged beyond safe reading."""


class VersionNotFound(StrataError):
    """Raised when a requested version does not exist in the archive."""


class IntegrityError(StrataError):
    """Raised when stored data does not match its content address."""

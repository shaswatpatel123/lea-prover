"""Lea exception types.

A single home for the errors Lea raises, so a traceback names the exact kind
of failure (and the UI/API layer can map an exception type to a response).
Later steps (tools, MCP, verifier) should add their own `LeaError` subclasses
here rather than raising bare exceptions.
"""


class LeaError(Exception):
    """Base for every error Lea raises. Catch this to catch anything Lea-specific."""


class ConfigError(LeaError):
    """Base for all configuration problems (bad file, bad key, bad value)."""


class ConfigFormatError(ConfigError):
    """The config (or a section of it) is not a mapping — e.g. a list/scalar at top level."""


class UnknownConfigKeyError(ConfigError):
    """A key that isn't recognized in its section — typically a typo, or a dead key."""


class MissingConfigKeyError(ConfigError):
    """A required key is absent after merging defaults with the user's config."""


class InvalidConfigValueError(ConfigError):
    """A key is present but its value has the wrong type (e.g. max_tokens: "lots")."""

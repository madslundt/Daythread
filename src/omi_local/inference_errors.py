class InferenceError(Exception):
    """Provider returned an invalid response; never forward its body to the caller."""


class InferenceTruncated(InferenceError):
    """The provider explicitly stopped at its output token limit."""

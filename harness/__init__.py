"""Agent-first harness for reproducible research and automated verification."""

from harness.spec import Check, Spec, SpecError, Step, load_spec

__version__ = "0.4.0"

__all__ = [
    "Check",
    "Spec",
    "SpecError",
    "Step",
    "load_spec",
    "__version__",
]

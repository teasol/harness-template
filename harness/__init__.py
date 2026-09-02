"""Agent-first harness for reproducible research and automated verification."""

from harness.verify.spec import Check, Spec, SpecError, Step, load_spec

__version__ = "0.8.0"

__all__ = [
    "Check",
    "Spec",
    "SpecError",
    "Step",
    "load_spec",
    "__version__",
]

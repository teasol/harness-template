"""Demo pipeline for the two-tier orchestration example.

Two modules, one plan (``plans/demo-pipeline.yaml``):
``data_gen`` produces a deterministic dataset; ``stats`` consumes it and
produces summary statistics. Each module is the deliverable of one Worker
task; the assembled flow is verified by ``configs/demo-pipeline.yaml``.
"""

__version__ = "0.1.0"

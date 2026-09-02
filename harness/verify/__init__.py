"""The mechanical half: run a spec, judge it, and prove it repeats.

This layer knows nothing about Planners, plans or tasks. It takes an ordered
list of steps with machine-checkable assertions, runs them, hashes what they
produced, and says pass or fail. Orchestration is built on top of it and can be
removed without touching it — which is the point: the guarantees a task's
"done" rests on are decided here, by code that cannot be talked round.

Deliberately empty of re-exports. Import the module you need
(``from harness.verify import spec``): the layers above and below import each
other's modules, and a package that pulled its submodules in on import would
turn that into a circular import.
"""

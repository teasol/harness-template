"""Running the project's own tests, and recording which ones pass.

This layer used to ship the assertions too — four check types and a spec
language to arrange them. It does not any more, because "does the code work" is
not the same question in two projects: a log parser and a training loop need
different tests, and a vocabulary built for both fits neither. What every
project does share is a command and an exit code.

So a checklist is a set of items addressed as ``<module>:<name>``, each naming a
command the project already has. The harness runs them, records which pass, and
gates a module's "done" on them. Writing the test is the project's job; knowing
which tests exist and which of them pass is this layer's.

Deliberately empty of re-exports. Import the module you need
(``from harness.verify import checklist``): the layers above and below import
each other's modules, and a package that pulled its submodules in on import
would turn that into a circular import.
"""

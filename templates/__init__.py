"""Not a code package — the files `harness init` copies into a project.

It carries this file only because a wheel cannot ship a bare directory: data has
to belong to a package, so these install as `harness_templates` beside
`harness`. Nothing imports it; `harness.paths.template_root()` finds the
directory by path, in this checkout and in an installed environment alike.
"""

# Molecule scenario — instance_cloud_init

**Status:** deferred — no standalone scenario.

This is a sub-role of `instance`, invoked only via
`include_role: name: instance_cloud_init tasks_from: <phase>` from within
`instance`'s orchestration. It has no `tasks/main.yml` entrypoint, so
`molecule converge` against it in isolation has no meaningful target.

Test coverage for this role lives with its parent — see
[roles/instance/molecule/default/](../../../instance/molecule/default/).
A functional test of `instance` exercises all phase tasks.

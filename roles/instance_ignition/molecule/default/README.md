# Molecule scenario — instance_ignition

**Status:** deferred — no standalone scenario.

Sub-role of `instance`, invoked only via `include_role: tasks_from: <phase>`.
No `tasks/main.yml` entrypoint, so `molecule converge` in isolation has nothing
to run against.

Test coverage is via the parent — see
[roles/instance/molecule/default/](../../../instance/molecule/default/).

# WINDOWS-DEPLOYMENT-001 — verify the replacement Windows shop host

Owner steering on 2026-10-22 supersedes the Mac as deployment target. The engineering Mac and
its running demos remain intact. `docs/WINDOWS_ENGINEER_HANDOFF_2026-10-22.md` contains the current
scope, host inventory, preparation steps and blocker/acceptance matrix.

Keep Linux application containers, existing R1 trust boundaries and closed capabilities. Inspect
the actual Windows edition/architecture before choosing WSL2/Docker Desktop or a Linux VM. Native
Windows containers are not implemented. Mac launch agents, `security`, BSD `find` and `/Volumes`
paths cannot serve as Windows runtime evidence.

Done requires target inventory, tested Windows/guest startup and operating schedules, browser
trust/private exposure, separate encrypted archive/key custody, exact target image verification,
MFA/role/policy read-back, timed restore, real alert delivery and owner counter acceptance. Tests
and synthetic local evidence are recorded distinctly. The assigned engineer must implement/test
host adaptations and retain missing-service refusals; no customer data or capability is authorized
by the source push. No production target was accessible in the current Mac session.

# Task packet: BACKUP-RESTORE-001

Goal: implement continuous PostgreSQL recovery and a recorded restore drill meeting RPO <=15 minutes
and RTO <=4 hours before the first real order.

Evidence must cover encrypted off-host recovery, audit and rule integrity, historical quote
reconstruction, selected recovery-point age, and outbox recovery without duplicate delivery. Local
configuration alone cannot satisfy the real restore-drill evidence requirement.


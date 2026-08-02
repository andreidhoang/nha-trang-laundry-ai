# Staff PWA slice

Mobile-first, same-origin staff shell for authenticated quote, order, approval, incident, manual-send,
and queue-recovery operations. Approval decisions remain unavailable in the shell because the current
API does not expose the bound resource preview needed to prevent blind approval. Manual-send controls
require exact server-bound versions and hashes, and the queue view has no replay control.

The service worker caches only the static `/staff/` shell. Material commands call the typed internal
API directly, are disabled while offline, and are never written to local storage, IndexedDB, a
background-sync queue, or any retry buffer. Offline operation is therefore read-only.

The API serves this directory at `/staff/` when the assets are present. No customer/public route,
provider credential, direct-send control, or embedded secret exists in this application.

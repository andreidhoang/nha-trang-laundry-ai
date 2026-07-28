# Staff PWA slice

Mobile-first, same-origin staff shell for authenticated order-board and approval-queue reads.
Material commands remain on the typed internal API and are not exposed as blind UI actions: a future
screen must render and verify the bound resource before allowing an approval decision.

The API serves this directory at `/staff/` when the assets are present. No customer/public route,
provider credential, direct-send control, or embedded secret exists in this application.

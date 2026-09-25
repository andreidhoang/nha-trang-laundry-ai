#!/bin/bash
# Schedule the pilot week's checks and its daily base backup, as launch agents.
#
# `cron` is the wrong mechanism here: on macOS a job whose time passes while the machine is asleep
# is not run, and never catches up. A laptop at a shop counter is asleep every night, so the daily
# base backup would simply never happen and nothing would report that. `launchd` runs a missed
# `StartCalendarInterval` job once on wake, which is the behaviour a shop needs.
#
# See deploy/shop-till/README.md for the two macOS traps that produce a job which looks scheduled
# and never runs.

set -euo pipefail

LABEL_PREFIX="com.giatlasachcong"
AGENT_DIRECTORY="$HOME/Library/LaunchAgents"
LOG_DIRECTORY="$HOME/Library/Logs/giatlasachcong"
REPOSITORY="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT="nha-trang-laundry-shop"

agents=(checks-data checks-host base-backup)

uninstall() {
    for name in "${agents[@]}"; do
        local plist="$AGENT_DIRECTORY/$LABEL_PREFIX.$name.plist"
        launchctl bootout "gui/$(id -u)/$LABEL_PREFIX.$name" 2>/dev/null || true
        rm -f "$plist"
        echo "removed $LABEL_PREFIX.$name"
    done
    exit 0
}

[ "${1:-}" = "--uninstall" ] && uninstall

: "${R1_LOCAL_ARCHIVE_PATH:?set R1_LOCAL_ARCHIVE_PATH to the archive directory before installing}"

if [ ! -d "$R1_LOCAL_ARCHIVE_PATH" ]; then
    echo "install: $R1_LOCAL_ARCHIVE_PATH does not exist." >&2
    echo "  If that is an external drive, attach it first. A bind mount to a missing path is" >&2
    echo "  created as an empty root-owned directory and every archive write then fails." >&2
    exit 1
fi

case "$REPOSITORY" in
    "$HOME"/Documents/*|"$HOME"/Desktop/*|"$HOME"/Downloads/*)
        echo "install: warning — the checkout is under a TCC-protected directory." >&2
        echo "  macOS will refuse these agents access with no useful error. Either grant" >&2
        echo "  /bin/bash Full Disk Access in System Settings → Privacy & Security, or move the" >&2
        echo "  checkout somewhere else (~/laundry) and re-run." >&2
        ;;
esac

mkdir -p "$AGENT_DIRECTORY" "$LOG_DIRECTORY"

# `DEC-025`: one Telegram message to the owner, from a separate bot. The token and the chat live in
# host files beside the other secrets -- gitignored, 0600, never in this repository and never on a
# command line. The plists carry the *paths*; `scripts/relay_shop_alert.py` reads the values.
SECRETS="$REPOSITORY/.shop/secrets"
ALERT_TOKEN_FILE="${R1_ALERT_TELEGRAM_TOKEN_FILE:-$SECRETS/alert_telegram_token}"
ALERT_CHAT_FILE="${R1_ALERT_TELEGRAM_CHAT_ID_FILE:-$SECRETS/alert_telegram_chat_id}"
for credential in "$ALERT_TOKEN_FILE" "$ALERT_CHAT_FILE"; do
    if [ ! -r "$credential" ]; then
        echo "install: WARNING — $credential does not exist." >&2
        echo "  Until it does, every failing check logs 'ALERT NOT DELIVERED' and exits 3, and" >&2
        echo "  nobody's phone hears about it. docs/runbooks/shop-till-mac.md §5 says how to make it." >&2
    fi
done

# A launchd plist is XML. `&&` in a <string> is not, and `launchctl bootstrap` refuses the whole
# file -- which is what the first version of this script wrote for two of its three agents.
# `sed` rather than `${v//&/&amp;}`: bash 5.2 reads `&` in a replacement as "the match", macOS's
# /bin/bash 3.2 does not, and this has to mean the same thing under both.
xml() {
    printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

write_agent() {
    local name="$1" schedule="$2" script="$3"
    local plist="$AGENT_DIRECTORY/$LABEL_PREFIX.$name.plist"
    cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL_PREFIX.$name</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>$(xml "$script")</string>
  </array>
  $schedule
  <key>WorkingDirectory</key><string>$(xml "$REPOSITORY")</string>
  <key>StandardOutPath</key><string>$(xml "$LOG_DIRECTORY/$name.log")</string>
  <key>StandardErrorPath</key><string>$(xml "$LOG_DIRECTORY/$name.log")</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>R1_LOCAL_ARCHIVE_PATH</key><string>$(xml "$R1_LOCAL_ARCHIVE_PATH")</string>
    <key>R1_ALERT_TELEGRAM_TOKEN_FILE</key><string>$(xml "$ALERT_TOKEN_FILE")</string>
    <key>R1_ALERT_TELEGRAM_CHAT_ID_FILE</key><string>$(xml "$ALERT_CHAT_FILE")</string>
    <key>R1_ALERT_LOG_FILE</key><string>$(xml "$LOG_DIRECTORY/alert-delivery.log")</string>
    <key>R1_DATA_CHECKS_DATABASE_URL_FILE</key><string>$(xml "$SECRETS/migration_database_url")</string>
  </dict>
</dict>
</plist>
PLIST
    launchctl bootout "gui/$(id -u)/$LABEL_PREFIX.$name" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
    echo "installed $LABEL_PREFIX.$name"
}

every_five_minutes='<key>StartInterval</key><integer>300</integer>'
# `StartCalendarInterval`, not an interval: a missed calendar job runs once on the next wake, which
# is exactly what a laptop that sleeps overnight needs. 02:30 is chosen because the shop is shut.
nightly='<key>StartCalendarInterval</key><dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>30</integer></dict>'

# Every check goes through the host relay (`SHOP-ALERT-DELIVERY-001`). The check prints its alert as
# one JSON line and sends nothing; the relay, running here on the host where there is internet,
# delivers it -- and exits 3 with a line in alert-delivery.log if it cannot.
relay="cd \"$REPOSITORY\" && .venv/bin/python scripts/relay_shop_alert.py"

# The data checks run inside the database's own network, as the postgres uid: `postgres` publishes
# no port and sits only on `internal: true` networks, so nothing on the host can reach it, and the
# base-backup marker lives in a 0700 directory owned by that uid. That network has no route out,
# which is why the relay above -- not the check -- is what talks to Telegram.
#
# The migration identity's URL arrives on stdin (`-i`, `--database-url-stdin`), read by the relay
# from the host file. It used to be `-e DATABASE_URL="$(cat …)"`, which put the password in the
# argument list of `docker` -- readable by `ps` -- and in the container's config, readable by
# `docker inspect`, for as long as the container existed.
data_checks="$relay --label checks-data \
  --stdin-file \"\$R1_DATA_CHECKS_DATABASE_URL_FILE\" -- \
  docker run --rm -i \
  --network ${PROJECT}_database-private --user 70:70 \
  -v \"$REPOSITORY:/repo:ro\" -w /repo -e HOME=/tmp \
  -v ${PROJECT}_pgdata:/pgdata:ro \
  -v ${PROJECT}_pgbackupstaging:/staging:ro \
  -e R1_RECOVERY_MODE=self-managed \
  -e R1_PGDATA_PATH=/pgdata -e R1_BASE_BACKUP_MARKER=/staging/last-success \
  --entrypoint python nha-trang-laundry-api:local \
  scripts/check_shop_operations.py --database-url-stdin \
  --check wal --check base --check volume --emit-alert"

# The host checks need the Docker socket and the console's own name over TLS, neither of which
# exists inside the network. On the till the console is on loopback, so the name resolves through
# /etc/hosts rather than the router. `/readyz`, not `/healthz`: the console answering while its
# database is gone is the shop being closed (`OPS-HARDENING-002`).
host_checks="$relay --label checks-host -- \
  env R1_CONSOLE_HEALTH_URL=https://console.giatlasachcong.lan:8443/readyz \
  R1_CONSOLE_CA_FILE=\"$REPOSITORY/.shop/ca/ca.crt\" \
  .venv/bin/python scripts/check_shop_operations.py --check flags --check console --emit-alert"

base_backup="cd \"$REPOSITORY\" && docker compose \
  -f compose.r1.yaml -f compose.shop-local.yaml -f compose.shop-till.yaml \
  --profile self-managed-database exec -T postgres /usr/local/bin/base-backup.sh"

write_agent checks-data "$every_five_minutes" "$data_checks"
write_agent checks-host "$every_five_minutes" "$host_checks"
write_agent base-backup "$nightly" "$base_backup"

echo
echo "archive:  $R1_LOCAL_ARCHIVE_PATH"
echo "logs:     $LOG_DIRECTORY"
echo "watch:    tail -f $LOG_DIRECTORY/checks-data.log"
echo "alerts:   tail -f $LOG_DIRECTORY/alert-delivery.log"
echo "status:   launchctl list | grep giatlasachcong    # 3 = an alert was NOT delivered"

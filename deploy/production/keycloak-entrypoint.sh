#!/bin/sh
# Read the database password from its mounted secret, then hand over to Keycloak.
#
# **Keycloak has no `_FILE` convention.** `KC_DB_PASSWORD_FILE` and
# `KC_BOOTSTRAP_ADMIN_PASSWORD_FILE` are not options it knows; it ignores them silently and then
# behaves as though the value were simply absent. Measured by starting the stack: the bootstrap
# admin produced "bootstrap-admin-username available only when bootstrap admin password is set" on
# a restart loop, and the database password produced a container that never became ready. Both look
# like configuration mistakes and neither says which setting is at fault.
#
# Reading the file here rather than putting the password in `environment:` is also better than the
# thing it replaces: a value in compose is in `docker inspect`, in every environment dump, and in
# the shell history of whoever wrote it. This one exists only inside the process.

set -eu

password_file="${KC_DB_PASSWORD_FILE:-/run/secrets/keycloak_database_password}"
if [ -r "$password_file" ]; then
    KC_DB_PASSWORD="$(cat "$password_file")"
    export KC_DB_PASSWORD
    unset KC_DB_PASSWORD_FILE
fi

exec /opt/keycloak/bin/kc.sh "$@"

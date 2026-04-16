#!/bin/sh
set -e

# Install udev rules and reload if running as root
if [ "$(id -u)" = "0" ]; then
    if [ -f /app/50-hasseb.rules ]; then
        cp /app/50-hasseb.rules /etc/udev/rules.d/50-hasseb.rules
        udevadm control --reload-rules 2>/dev/null || true
        udevadm trigger 2>/dev/null || true
    fi
fi

exec "$@"

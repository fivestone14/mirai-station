#!/usr/bin/env bash
# scrub.sh — refuse machine-identifying strings in the tracked tree.
#
# Every pattern here is a SHAPE, never the literal secret. This file ships in the
# PUBLIC repo too, so spelling out the real hostname, LAN address or e-mail would
# be the very leak it exists to stop.
#
# Three habits have each re-leaked once already (2026-08-06): the personal commit
# e-mail, the local -0700 stamp (see commit-hygiene.sh) and the real LAN IP
# creeping back into a fixture as a "realistic" value.
#
# Exit 0 = clean.  Exit 1 = something identifying is in the tree.

set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

fail=0
# The scanner's own patterns read as hits; never scan the scripts themselves.
EXCL=':(exclude).github/scripts/*'

report() {                                  # report <label> <hits>
    printf '\nFAIL  %s\n' "$1"
    printf '%s\n' "$2" | sed 's/^/        /'
    fail=1
}

scan() {                                    # scan <label> <regex> [allow-regex]
    local label="$1" re="$2" allow="${3:-}" hits
    hits=$(git grep -nIE -e "$re" -- . "$EXCL" || true)
    if [ -n "$allow" ] && [ -n "$hits" ]; then
        hits=$(printf '%s\n' "$hits" | grep -Ev "$allow" || true)
    fi
    [ -n "$hits" ] && report "$label" "$hits"
    return 0
}

scan 'personal e-mail address (commit and write as the noreply identity)' \
     '[A-Za-z0-9._%+-]+@(gmail|yahoo|outlook|hotmail|icloud|proton|protonmail|aol|live|me|mac)\.[A-Za-z]{2,}'

scan 'absolute home directory — it names the account (use ~ or $HOME)' \
     '/Users/[A-Za-z0-9._-]+'

scan 'machine hostname' \
     '[A-Za-z0-9]+-[Mm]ac-?(mini|Mini|[Bb]ook|Pro|pro|Studio|studio|Air|air)'

scan 'tailnet hostname — it names the tailnet' \
     'tail[0-9a-f]{4,}\.ts\.net'

scan 'ntfy topic — a topic IS the credential; use a placeholder in fixtures' \
     'ntfy\.sh/[A-Za-z0-9_-]{10,}'

# Tailscale CGNAT space, minus the network literal the host guards are built on
# and the fabricated address the host-guard tests use.
scan 'Tailscale CGNAT address' \
     '100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}' \
     '100\.64\.0\.0/10|100\.101\.102\.103'

# Private LAN space: only the three sanctioned fixture addresses may appear.
# Matched by value rather than by line so one bad octet cannot hide behind a
# good address on the same line.
IP_RE='(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3})'
SANCTIONED='^(192\.168\.1\.40|10\.0\.0\.7|172\.16\.4\.9|10\.0\.0\.0|192\.168\.0\.0|172\.16\.0\.0)$'
bad_ips=$(git grep -hIoE -e "$IP_RE" -- . "$EXCL" | sort -u | grep -Ev "$SANCTIONED" || true)
if [ -n "$bad_ips" ]; then
    hits=""
    while read -r ip; do
        [ -n "$ip" ] || continue
        hits="$hits$(git grep -nIF -e "$ip" -- . "$EXCL")
"
    done <<EOF
$bad_ips
EOF
    report 'private LAN address that is not a sanctioned fixture (use 192.168.1.40 / 10.0.0.7 / 172.16.4.9)' "$hits"
fi

if [ "$fail" = 0 ]; then
    echo "scrub: clean"
else
    echo
    echo "scrub: the strings above identify the machine or its owner. Replace them"
    echo "       with placeholders before this reaches a public commit."
fi
exit "$fail"

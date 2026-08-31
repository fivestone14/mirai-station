#!/usr/bin/env bash
# commit-hygiene.sh — the commit metadata itself is a leak surface.
#
#   identity  every commit must be authored by the noreply address. Global git
#             config still holds the real one, so any clone without the repo-local
#             override quietly publishes it.
#   stamp     every commit must be stamped +0000. A local commit stamps the
#             machine's own offset, which pins the region on every future commit.
#
# RANGE is any revision range git log understands, and it is WORD-SPLIT on use,
# so a caller with no single base may pass '<sha> --not --remotes=origin' — the
# commits that are genuinely new — rather than guessing a depth.
#
# The HEAD~20 fallback below is a last resort and it is a BAD one, because it is
# a fixed slice of history rather than a diff: it re-audits commits that already
# landed, so one bad commit fails every later check until it scrolls out of the
# window, and it says nothing about the change in hand. Callers should always
# pass a real range. Kept only so the script cannot silently check nothing.

set -uo pipefail

EXPECTED="${EXPECTED_EMAIL:-fivestone14@users.noreply.github.com}"
RANGE="${RANGE:-}"

# Usable only if git itself can resolve it — covers 'a..b', a bare sha, and the
# '--not --remotes' form. A new branch, a first push or a force-push may give
# none of those.
if [ -z "$RANGE" ] || ! git rev-list --quiet $RANGE >/dev/null 2>&1; then
    if git rev-parse --quiet --verify 'HEAD~20' >/dev/null 2>&1; then
        RANGE='HEAD~20..HEAD'
    else
        RANGE='HEAD'
    fi
fi

echo "checking $RANGE"
fail=0
n=0

while IFS="$(printf '\t')" read -r sha ae ce ad cd subj; do
    [ -n "$sha" ] || continue
    n=$((n + 1))
    short="${sha:0:8}"
    for pair in "author:$ae" "committer:$ce"; do
        who="${pair%%:*}"; addr="${pair#*:}"
        if [ "$addr" != "$EXPECTED" ]; then
            printf 'FAIL  %s  %s e-mail is not the noreply identity\n        %s\n        %s\n' \
                   "$short" "$who" "$addr" "$subj"
            fail=1
        fi
    done
    for pair in "author:$ad" "committer:$cd"; do
        who="${pair%%:*}"; raw="${pair#*:}"
        off="${raw##* }"
        if [ "$off" != "+0000" ]; then
            printf 'FAIL  %s  %s date is stamped %s, not +0000 — it pins the region\n        %s\n' \
                   "$short" "$who" "$off" "$subj"
            fail=1
        fi
    done
  # unquoted on purpose — RANGE may carry several tokens (see the header)
done < <(git log --format='%H%x09%ae%x09%ce%x09%ad%x09%cd%x09%s' --date=raw $RANGE)

if [ "$fail" = 0 ]; then
    echo "commit hygiene: $n commit(s) clean"
else
    echo
    echo "commit hygiene: rewrite the offending commits before pushing. Dates are"
    echo "                fixed by re-committing with GIT_AUTHOR_DATE/GIT_COMMITTER_DATE"
    echo "                forced to +0000; identity by setting the repo-local user.email."
fi
exit "$fail"

#!/usr/bin/env bash
# commit-hygiene.sh — the commit metadata itself is a leak surface.
#
#   identity  every commit must be authored by the noreply address. Global git
#             config still holds the real one, so any clone without the repo-local
#             override quietly publishes it.
#   stamp     every commit must be stamped +0000. A local commit stamps the
#             machine's own offset, which pins the region on every future commit.
#
# RANGE may be set by the caller (a push's before..after); otherwise the last 20
# commits are checked, which also covers a brand-new branch.

set -uo pipefail

EXPECTED="${EXPECTED_EMAIL:-fivestone14@users.noreply.github.com}"
RANGE="${RANGE:-}"

# A new branch, a first push or a force-push gives no usable base.
if [ -z "$RANGE" ] || ! git rev-parse --quiet --verify "${RANGE%%..*}^{commit}" >/dev/null 2>&1; then
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
done < <(git log --format='%H%x09%ae%x09%ce%x09%ad%x09%cd%x09%s' --date=raw "$RANGE")

if [ "$fail" = 0 ]; then
    echo "commit hygiene: $n commit(s) clean"
else
    echo
    echo "commit hygiene: rewrite the offending commits before pushing. Dates are"
    echo "                fixed by re-committing with GIT_AUTHOR_DATE/GIT_COMMITTER_DATE"
    echo "                forced to +0000; identity by setting the repo-local user.email."
fi
exit "$fail"

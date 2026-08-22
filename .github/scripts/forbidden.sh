#!/usr/bin/env bash
# forbidden.sh — the public build must never carry the vendor-scraper modules.
#
# The public repo is a STRIPPED VARIANT, not a mirror: three modules were removed
# from all of its history because they fetch an undocumented vendor backend behind
# a spoofed browser User-Agent, and say in their own comments that the spoof is the
# bypass. Fine to run privately; a named terms-of-service admission to publish.
#
# Prose about them stays — the negative result (the vendor sign measured
# incoherent, so the layer was never consulted) is the useful part, and it is
# already documented here. What must never come back is the CODE: the files
# themselves, a live import of one, the spoofed UA, or a URL into the backend.
#
# Run this against a PUBLIC tree — the private one is supposed to fail it.

set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

fail=0
EXCL=':(exclude).github/scripts/*'

report() {
    printf '\nFAIL  %s\n' "$1"
    printf '%s\n' "$2" | sed 's/^/        /'
    fail=1
}

# 1. the stripped modules and their tests, by filename
for f in uw_periscope.py uw_coherence.py gex_uw_bridge.py test_uw_bridge.py test_gamma_roll.py; do
    hits=$(git ls-files "*/$f" "$f")
    [ -n "$hits" ] && report "stripped module is back in the tree: $f" "$hits"
done

# 2. a live import of one — prose may name them, code may not reach for them
hits=$(git grep -nIE -e '^[[:space:]]*(import|from)[[:space:]]+(uw_periscope|uw_coherence|gex_uw_bridge)([[:space:]]|$|\.)' -- . "$EXCL" || true)
[ -n "$hits" ] && report 'import of a stripped module' "$hits"

# 3. the scraper's own signature
hits=$(git grep -nIE -e 'Mozilla/' -- . "$EXCL" || true)
[ -n "$hits" ] && report 'spoofed browser User-Agent — the documented ToS bypass' "$hits"

hits=$(git grep -nIE -e 'https?://[A-Za-z0-9.-]*unusualwhales\.com' -- . "$EXCL" || true)
[ -n "$hits" ] && report 'URL into the vendor backend' "$hits"

if [ "$fail" = 0 ]; then
    echo "forbidden: clean — no scraper code in this tree"
else
    echo
    echo "forbidden: this is the check that keeps a terms-of-service admission out"
    echo "           of a public repo. Do not weaken it; strip the code instead."
fi
exit "$fail"

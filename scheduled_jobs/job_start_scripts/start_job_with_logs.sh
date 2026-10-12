#!/usr/bin/env bash
# start_job_with_logs.sh — launchd's one way into every station job.
#
#   start_job_with_logs.sh <job> <command> [args...]
#
# launchd takes StandardOutPath and StandardErrorPath as literal paths and expands nothing
# in them, and a literal home folder in a tracked plist names the account (the scrub refuses
# it), so every plist starts its job as `bash -lc` on this script, which bash hands $HOME. It
# makes the logs folder (launchd never creates folders), sends everything the job prints to
# the job's own two files under data/operations/logs/ and then becomes the job.
#
# <job> is the launchd Label without its com.mirai-station. prefix (spx-jev-tape); its files
# are <job>_output_log.txt and <job>_errors_log.txt with the hyphens as underscores
# (spx_jev_tape_output_log.txt), the stores station_paths.LAUNCHD_JOB_OUTPUT_LOG and
# LAUNCHD_JOB_ERRORS_LOG name. <command> is a run script in this folder by name
# (run-spx-jev.sh) or an absolute program (/usr/bin/caffeinate). Nothing rotates the logs:
# launchd appended to /tmp/mirai-station.<job>.{out,err} the same way until 2026-10-11.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LOGS="$(cd "${SCRIPT_DIR}/../.." && pwd)/data/operations/logs"

job="${1:?usage: start_job_with_logs.sh <job> <command> [args...]}"; shift
command="${1:?usage: start_job_with_logs.sh <job> <command> [args...]}"; shift
case "$command" in /*) ;; *) command="${SCRIPT_DIR}/${command}" ;; esac

mkdir -p "$LOGS"
exec >>"${LOGS}/${job//-/_}_output_log.txt" 2>>"${LOGS}/${job//-/_}_errors_log.txt"
exec "$command" "$@"

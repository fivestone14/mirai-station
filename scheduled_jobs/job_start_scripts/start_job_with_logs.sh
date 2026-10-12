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
# (run-spx-jev.sh) or an absolute program (/usr/bin/caffeinate).
#
# The logs rotate daily, here, as the job starts (cleanup phase 9, 2026-10-12): a log last
# written on an earlier day (this Mac's clock) is moved to data/operations/logs/older/<that
# day>/ and gzipped, so each job's two files hold today only; the rotated days are kept
# LOG_KEPT_DAYS days (Will's choice), and an older day's folder goes. A job that never
# restarts (a KeepAlive daemon) rotates at its next start. Nothing here can stop the job: a
# rotation that fails leaves the log where it was. The .jsonl run logs beside them are not
# touched (the Claude forecast prunes its own).
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LOGS="$(cd "${SCRIPT_DIR}/../.." && pwd)/data/operations/logs"

job="${1:?usage: start_job_with_logs.sh <job> <command> [args...]}"; shift
command="${1:?usage: start_job_with_logs.sh <job> <command> [args...]}"; shift
case "$command" in /*) ;; *) command="${SCRIPT_DIR}/${command}" ;; esac

mkdir -p "$LOGS"
LOG_KEPT_DAYS=14
today="$(date +%F)"

rotate_log() {   # $1: a job log; moved to older/<its last-modified day>/ and gzipped when that day is before today
  local log="$1" day kept
  [[ -f "$log" ]] || return 0
  day="$(date -r "$log" +%F 2>/dev/null)" || return 0
  [[ "$day" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && "$day" < "$today" ]] || return 0
  mkdir -p "${LOGS}/older/${day}" || return 0
  kept="${LOGS}/older/${day}/${log##*/}"
  if [[ -e "${kept}.gz" ]]; then       # that day was rotated already (a clock change): add to it
    gzip -c "$log" >>"${kept}.gz" && rm -f "$log"
  else
    mv "$log" "$kept" && gzip -f "$kept"
  fi
}

for log in "${LOGS}/${job//-/_}_output_log.txt" "${LOGS}/${job//-/_}_errors_log.txt"; do
  rotate_log "$log" 2>/dev/null
done
oldest_kept="$(date -v-"${LOG_KEPT_DAYS}"d +%F 2>/dev/null || date -d "${LOG_KEPT_DAYS} days ago" +%F)"
for folder in "${LOGS}/older/"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]; do
  [[ -d "$folder" && "${folder##*/}" < "$oldest_kept" ]] && rm -rf "$folder" 2>/dev/null
done

exec >>"${LOGS}/${job//-/_}_output_log.txt" 2>>"${LOGS}/${job//-/_}_errors_log.txt"
exec "$command" "$@"

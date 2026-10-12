# ntfy — where the setup lives now

The alert channel is documented in the plugin's operations runbook, in the
section "ntfy alert channel":

    ~/.claude/plugins/mirai-station/docs/OPERATIONS.md

It covers the one thing that matters: the topic name is the credential, it
lives in the Keychain under `mirai-station-ntfy`, and how to rotate it and
re-subscribe the phone app.

The push itself is `runtime/watch/intraday/push_ntfy.py`; what the SPX tick
sends (fresh paper fires, broken walls, the end-of-day direction score) is in
`runtime/watch/intraday/gex_alerts.py`.

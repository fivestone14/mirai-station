# ntfy — where the setup lives now

The alert channel is documented in the station's operations runbook, in the
section "ntfy alert channel":

    ~/mirai_awakening/docs/how_to_run/OPERATIONS.md

It covers the one thing that matters: the topic name is the credential, it
lives in the Keychain under `mirai-station-ntfy`, and how to rotate it and
re-subscribe the phone app.

The push itself is `alerts_and_phone_pushes/watch/intraday/push_ntfy.py`; what the SPX tick
sends (fresh paper fires, broken walls, the end-of-day direction score) is in
`alerts_and_phone_pushes/watch/intraday/gex_alerts.py`.

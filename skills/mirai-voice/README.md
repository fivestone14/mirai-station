# mirai-voice — the SNDK desk, spoken

Press the talk button on the Nightglass SNDK tab and hold an open conversation
about the board. The organs, in the order sound travels:

| Module | Organ | What it does |
|---|---|---|
| `ears.py` | Mirai-Ears | Parakeet (MLX) — PCM in, transcript out, ~160 ms |
| `jargon.py` | — | fixes what the ears mishear ("jex" → GEX, "san disk" → SNDK) |
| `convo.py` | the mind-link | one Claude session per trading day (subscription auth, streaming); injects the reader's own scene (`sndk_read.build_scene`) per turn; RAG CLI is the only tool |
| `doctrine.py` | — | the standing spoken-agent contract (appended system prompt) |
| `mouth.py` | Mirai-Mouth | Kokoro TTS at 24 kHz, `say` fallback |
| `voice_server.py` | the sidecar | WebSocket :8788 — server-side VAD turn-taking, barge-in, 3-min auto-off |
| `repl.py` | test bench | the whole loop in a terminal: `repl.py [--mic|--mute]` |
| `bench.py` | test bench | STT/TTS latency + mis-hearing harvest |

Service: `runtime/launchd/com.mirai-station.voice.plist` (daemon, KeepAlive) →
`runtime/scripts/run-voice.sh`. Kill switch `MIRAI_VOICE_DISABLE=1` (shell AND
python). Logs: `/tmp/mirai-station.voice.{out,err}`. Conversation log:
`state/voice/convo-{date}.jsonl`; session continuity: `state/voice/session.json`.

Scope: SNDK only, by doctrine. Mic needs a secure context — localhost on the
Mac works today; the iPad needs the HTTPS cert step (plan phase 4).

Born 2026-08-03 (research artifact 315650cc; plan calm-rolling-mountain).

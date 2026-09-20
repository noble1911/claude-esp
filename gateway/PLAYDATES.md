# Pet playdates protocol (version 1)

Separate WebSocket `/play`, no AI calls. Only registered user IDs explicitly
listed in `PET_PLAYDATE_GROUPS` and in the presented token's allowed-users list
can connect. Different groups cannot see or invite one another. Normal `/ws`
voice clients are unchanged. Configure `PET_PLAYDATE_DB=/data/playdates.sqlite3`
and retain the mounted data volume across upgrades and restarts.

Hello: `{"type":"hello","proto":1,"artwork":3,"user_id":"...","device_token":"...","pet":{"id":"16 lower-case hex digits","name":"Sprout","character":0,"stage":1}}`.
Character is 0–6; stage is 1–5; names are 1–15 ASCII letters/spaces/apostrophes/hyphens,
starting with a letter. Invalid or incompatible registration closes the socket.

Commands after hello:

- `join` with current `pet`: advertise availability and synchronize identity.
- `leave`: cancel invitation or room and stop advertising.
- `invite` with another household `user`: only two available, distinct pets.
- `accept` / `decline` with current `invite` ID. Only the recipient can accept.
- `pass` with current `room` ID and `seq`: only the current player, once per turn.
- `again` with current `room`: starts a new round only when both players opt in.
- `ack` with decimal-string receipt `id` and hex `pet`: acknowledge the oldest
  pending reward for this device and pet after its atomic local save.
- `ping` every 5 seconds: request current state and keep the session alive.

Server messages are complete `play_state` snapshots, suitable for coalescing.
`phase` is closed/lobby/outgoing/incoming/playing/waiting/finished. `peers` contains
up to four available household profiles with `user`. Invitations include `invite`
and `peer`. Rooms include `room`, `seq` (0–10), `peer`, `online`, `my_turn`, and
`again` (this player's replay vote). `notice` is a short human-readable status.
`reward` is null or `{id,pet,friend,friends}`; IDs are strings, `friends` is the
durable distinct-friend count, capped at 65535. One completed round gives one
care star and 30 happiness; the first friend unlocks the buddy sticker. The
server commits both receipts and friendships before announcing completion.

Invitations expire after 30 seconds; disconnected peers have 45 seconds to
rejoin a room. Application heartbeat timeout is 18 seconds. There is no deadline
for an active player's turn. Minimum pass interval is 800ms to cover ball travel.
Disconnecting or restarting never discards pending reward receipts. SQLite
transactions are synchronous on the coordinator's single event-loop owner;
independent bounded socket writers prevent a slow peer from holding the game.

Run `python -m pytest -q`. `tests/test_playdates.py` covers coordinator fault
cases and a complete two-client exchange through actual WebSockets.

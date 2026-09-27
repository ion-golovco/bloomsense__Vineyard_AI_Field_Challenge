# Demo: 2 minutes, on stage

Start everything with one command from the repo root, at least 6 minutes before the slot: after new data the app builds its route plan once (about 5 minutes for 290 targets; later starts load it from disk in seconds):

```sh
scripts/demo.sh            # app on http://127.0.0.1:8000 and the Telegram bot; Ctrl+C stops both
HOST=0.0.0.0 scripts/demo.sh   # also lets a phone on the same Wi-Fi open http://<laptop-ip>:8000
```

The bot reads `TELEGRAM_BOT_TOKEN` from `.env`. Only one bot process may run per token, so stop any other copy first.

## Click path

| Time | Where | Do | Say |
|---|---|---|---|
| 0:00 | Telegram, phone | Send `V06-03` to @bloomsense_marcajbot | "The farmer never opens our app. They send a field name." |
| 0:15 | Telegram | Show the route image and scroll the numbered checklist; tap one Google Maps link | "Route, stops in walking order, what to check at each, and a map link." |
| 0:35 | Web app, laptop: `http://127.0.0.1:8000/?role=inspector#all-fields` | Show the whole farm: fields, the site route, the "What you save" card | "The inspector sees every field: 50 km of rows, one 11.5 km walk." |
| 0:55 | Same page | Press Start, tap a point on the map; press End, tap another; press Plan route | "Routes on request, and it tells you what it skipped and why." |
| 1:20 | Per field (left menu), pick V06-03 | Show the measurements block: rows, row length, canopy, inter-row area, gaps, waste | "Every number comes from the drone survey, in EPSG:32635 metres." |
| 1:40 | Header switch: Farmer | Show the farmer view of the same field and its score | "Same data, the farmer only sees their own field." |
| 1:55 | | Back to the slides | |

## If something fails

- **No internet (the bot needs it):** show `output/demo/5_telegram_V06-03.png` and the checklist text in `output/demo/5_telegram_V06-03.txt`.
- **App not loading:** use the screenshots in `output/demo/` (1 all fields, 2 per field, 3 measurements, 4 farmer view).
- **Plan route is slow:** the plan is still warming up after new data (about 5 minutes); skip that step and show the precomputed site route.
- Re-render the bot images offline: `cd backend && uv run --frozen python -m marcaj.telegram_bot --render V06-03 --out ../output/demo/5_telegram_V06-03.png`.

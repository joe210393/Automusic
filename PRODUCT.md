# Automusic Travel

## Platform
web

## Stack
Plain static HTML/CSS/JS for tourist + admin surfaces; FastAPI backend. (Inferred from repository; not a redesign of stack.)

## Users
Tourists at a physical destination (starting with Su'ao / 蘇澳), often on a phone outdoors. They want a souvenir song from the trip, not music-production tools.

## Job
Collect trip sounds + personal keywords → leave with a sung travel song in their voice (or a downloadable mix).

## Product mechanism
Destination content packs (stations → routes → sound tasks → moods) drive a guided journey that wraps an existing music engine (melody / arrangement / lyrics / voiceprint / SVS). Tourists never see MIDI, BPM, or model names.

## Surfaces
- `/` tourist experience: hub (pick station) → station landing → guided flow
- `/admin` content CMS for destinations / routes / moods
- `/web` engineering lab (out of tourist scope)
- `/s/{slug}` share page

## Constraints
- Keep `/web` and engine contracts intact
- Tourist copy in Traditional Chinese
- Touch-first, outdoor readability
- Keywords for lyrics come from the user, not system chips
- Content (stations, routes) is editable from admin, not hardcoded in UI

## Brand commitments
- Product name: Automusic
- Tourist-facing brand: 蘇澳拾音（SU'AO SOUND）
- Station naming pattern: 「{地名}站」
- Core line in use: 不是來做一首歌，是把這趟旅行帶回家

## Open
- Visual system under refresh (anti-AI-slop pass, 2026-08); DESIGN.md to be written after the quieter pass lands

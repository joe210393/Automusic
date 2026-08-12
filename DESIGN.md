# Automusic Travel — visual system

## World
**Sound Journey / 蘇澳拾音** — photo-led travel UI. Full-bleed place photography with CTAs parked in the image’s quiet zones (water, sky, pier), matching the user-supplied comps.

## Assets
## Screen → photo map
| Screen | Asset |
|--------|--------|
| Hub | `hero-train` |
| Station (蘇澳) | `hero-lighthouse` |
| 選擇旅程 | `bg-map` |
| 收集／錄音 | `hero-spring` |
| 關鍵字 | `hero-path` |
| 心情 | `hero-stairs` |
| 創作中 | `hero-train` |
| 聲紋 | `hero-market` |
| 成品 | `hero-harbor` |

Flow screens use full-bleed photo + frosted paper panel.

## Palette
| Token | Hex | Role |
|-------|-----|------|
| navy | `#1a2b4a` | primary buttons, pins, progress |
| paper | `#f7f4ef` | flow page ground |
| foam | `#ffffff` | panels |
| muted | `#5c6b7a` | secondary text |

## Type
- Display: `Noto Serif TC` (brief-pinned editorial headlines on photos)
- Body: `Noto Sans TC`

## Composition rules
1. Landing and station are full-viewport scenes, not card stacks.
2. Primary actions sit in lower-left / water negative space.
3. Soft left veil for type contrast; do not smother the photo.
4. Station choices are navy pill pins on the vista.
5. Flow steps use warm paper panels with the same navy controls.

## Motion
Light fade between screens; step-rail `scaleX`. Respect `prefers-reduced-motion`.

# Portal Theme — "Federal SOC Dark"

Chosen theme for the ATO Evidence Analysis Portal (July 2026 design pass).
It blends two directions that were prototyped in canvas mockups:

- **Palette**: SOC dark console — near-black blue-gray surfaces with a
  saturated blue accent and high-contrast status colors. Matches the
  Splunk/Grafana/SIEM tooling cyber analysts already use.
- **Edges and type**: USWDS conventions — square 2px corner radius and
  Public Sans for UI text with Roboto Mono for identifiers (run IDs,
  digests, blocker codes).

Implemented in `portal/src/index.css` (`.dark` block; the portal runs
dark-only via `<html class="dark">` in `portal/index.html`). No new
libraries — this is CSS variables plus two Google Fonts imports.

**Visual reference (open in any browser):**
[`docs/theme-mockups/federal-soc-dark-reference.html`](theme-mockups/federal-soc-dark-reference.html)
— workflow mockup, palette swatches, CSS variable table, and accessibility
contrast ratios.

## Palette

| Role (shadcn variable)   | Hex       | Usage                                    |
| ------------------------ | --------- | ---------------------------------------- |
| `--background`           | `#0d1117` | Page background                          |
| `--card` / `--popover`   | `#161b22` | Cards, panels, popovers                  |
| `--secondary` / `--muted`| `#1c2129` | Muted fills, selected rows, secondary buttons |
| `--border` / `--input`   | `#30363d` | Borders, input outlines                  |
| `--foreground`           | `#e6edf3` | Primary text                             |
| `--muted-foreground`     | `#8b949e` | Secondary/muted text                     |
| `--primary`              | `#1f6feb` | Primary buttons, focus ring (WCAG AA 4.6:1 with white text) |
| `--primary-foreground`   | `#ffffff` | Text on primary                          |
| `--destructive`          | `#f85149` | Errors, blockers, failed states          |
| `--sidebar`              | `#10141a` | Sidebar background                       |

Status colors: `Badge` variants in `portal/src/components/ui/badge.tsx` use
Tailwind `emerald-400` / `amber-400` text on 15%-tinted fills — the dark-mode
readable steps of those hues (the previous `*-700` steps were tuned for light
backgrounds).

## Accessibility

- Primary buttons: white on `#1f6feb` = 4.6:1, passes WCAG AA. The brighter
  `#2f81f7` from the original SOC mockup fails at 3.7:1 with white text and
  is not used for filled buttons.
- Body text `#e6edf3` on `#0d1117` ≈ 15:1; muted text `#8b949e` ≈ 6.2:1;
  destructive `#f85149` ≈ 5.6:1 — all pass AA on the page background.

## Shape and type

- `--radius: 0.125rem` (2px). shadcn's derived `--radius-sm`/`--radius-md`
  clamp to 0, so small controls render fully square — intentional, per the
  USWDS look.
- UI text: `Public Sans` (fallback: Source Sans Pro, system stack).
- Identifiers/code: `Roboto Mono`, applied to `code`, `kbd`, `pre`, `samp`,
  and Tailwind's `.font-mono`.
- Fonts load from Google Fonts in `index.css`; system fallbacks keep the
  portal usable offline.

## Provenance

Explored in canvas mockups (Cursor project folder, outside this repo):
`portal-style-directions.canvas.tsx` (USWDS Federal / SOC Dark Console /
Civic Light / Federal Dark blend) and `federal-dark-contenders.canvas.tsx`
(six background-and-accent variations). This document is the durable
record; the canvases are exploratory only.

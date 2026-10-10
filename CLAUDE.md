# housex-ping

Infra pings for HOUSE X (`.github/workflows/`) and the **carousel generator** (`carousel/`).

## Carousel: brief → finished slides

When Mohamad sends project info (text, brochure, photos) and asks for a carousel / شرائح / كاروسيل:

1. **Slug + folder**: `carousel/projects/<slug>/`. Copy any photos he uploaded into `images/`
   (pick the strongest exterior/aerial for `theme.bg` and the cover `theme.collage`).
2. **Write `project.json`** per `carousel/SCHEMA.md`. Copy rules:
   - Modern Standard Arabic, simplified and confident; premium positioning, no cheap hype, no invented facts.
     Missing data (e.g. distances) → ask, or drop that line; never fabricate numbers.
   - Cover hook sells the outcome (lifestyle / investment / citizenship), not the brochure name.
   - One `*accent*` per line at most, on a number or power word.
   - Stay inside the length guides in SCHEMA.md.
   - Use Latin digits (0-9) everywhere, Arabic lines included (`15 دقيقة`, `2+1`, `24/7`). The renderer also converts any ٠-٩ it finds.
   - Footer phone is always `+90 551 4000 200` unless Mohamad asks for another number.
3. **Render**: `node carousel/render.mjs carousel/projects/<slug>/project.json` (add `--story` if asked).
   It checks the project first: exit code 1 → fix the listed errors (missing text, 6-10 amenities, image files);
   read the warnings too (over-length text, no photos, a different phone). Exit code 2 → shorten the flagged text and re-render. Then look at the PNGs yourself before sending.
4. **Deliver** the PNGs from `out/` to the user, commit `project.json` + `images/` (not `out/`), push.

Brand: colours/logo/fonts follow the `housex-brand` skill. Logos live in `carousel/assets/`.
`carousel/projects/vadi-premium/` is the reference example.

## Open decisions (ask Mohamad before changing)

- ~~Footer phone~~ decided 2026-10-10: always `+90 551 4000 200` (not the brand skill's `+90 551 900 6600`) unless Mohamad says otherwise.
- ~~CTA badge contrast~~ decided 2026-10-10: slide-5 CTA badge is navy `#26247b` text on gold `#ffca05`.
- Only one template exists (project launch). Proposed next: "market news" and "Turkish citizenship" templates on the same project.json pipeline.

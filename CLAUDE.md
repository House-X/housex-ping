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
   - Use Arabic-Indic digits in Arabic lines, as in the example project.
3. **Render**: `node carousel/render.mjs carousel/projects/<slug>/project.json` (add `--story` if asked).
   Exit code 2 → shorten the flagged text and re-render. Then look at the PNGs yourself before sending.
4. **Deliver** the PNGs from `out/` to the user, commit `project.json` + `images/` (not `out/`), push.

Brand: colours/logo/fonts follow the `housex-brand` skill. Logos live in `carousel/assets/`.
`carousel/projects/vadi-premium/` is the reference example.

## Open decisions (ask Mohamad before changing)

- Footer phone: builder uses `+90 551 4000 200`; brand skill lists `+90 551 900 6600`. Kept the builder's number.
- CTA badge on slide 5 is red text on navy (weak contrast). Proposed: navy text on gold `#ffca05`.
- Only one template exists (project launch). Proposed next: "market news" and "Turkish citizenship" templates on the same project.json pipeline.

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
   - Slide 5 (CTA) never mentions prices or payments: no price, down payment, instalments, discount, currency.
     The renderer refuses such words there (exit 1).
   - Slide-5 `desc`: 2 short independent sentences as an array, ≤ 42 chars each; each gets its own line.
   - Numbers in the slide-1 bullets are all red automatically (with their م²); no need to mark them with `*…*`.
   - Footer phone is always `+90 551 4000 200` unless Mohamad asks for another number.
3. **Write `social.json`** next to `project.json`: the Instagram caption and the Facebook post for this project
   (format in SCHEMA.md). Same copy rules as the slides; no prices or payments; end with the contact line and
   `+90 551 4000 200`; Instagram ≈ 10-15 hashtags (Arabic + English), Facebook a longer factual post with 4-6.
4. **Render**: `node carousel/render.mjs carousel/projects/<slug>/project.json`. It always renders the feed (1080×1350)
   and the story (1080×1920) set, builds `<slug>-social.docx` from `social.json`, and copies the latest set to
   `Documents/HOUSE X Carousels/<slug>/` (`Feed/`, `Story/`, the .docx), replacing the previous one.
   It checks the project first: exit code 1 → fix the listed errors (missing text, 6-10 amenities, image files);
   read the warnings too (over-length text, no photos, a different phone). Exit code 2 → shorten the flagged text and re-render. Then look at the PNGs yourself before sending.
5. **Deliver** the feed PNGs to the user (story too if asked) and tell him the folder; commit `project.json`,
   `social.json` + `images/` (not `out/`), push.

## Carousel from a link

When Mohamad sends a project page URL instead of a brief, the link *is* the brief:

1. **Read the page in the browser** (pages are often client-rendered, so a plain fetch misses the text).
   Take the visible text (`document.body.innerText` when `get_page_text` comes back nearly empty) and the
   gallery image URLs from the page HTML. Use only what the page states.
2. **Copy goes through step 2 above**. Leave out by default, and tell him you did:
   prices and "starts from", time-limited discounts, estimated ROI / rental yield / growth figures.
   Record the page in `project.json` as `"source": "<url> (<date>)"`.
3. **Photos**: list them for Mohamad (count, total size, host) and download only after he says yes:
   `node carousel/fetch-images.mjs <slug> <url> <url> …` → `images/01.webp`, `02.webp` …
   Then look at every photo: drop floor plans, icons and anything with another company's logo or watermark;
   use the strongest exterior/aerial for `theme.bg` and the cover `theme.collage`.
4. Continue with **social.json** (from the same page facts), **Render** and **Deliver** above.

Site notes:

| site | page | text | gallery |
|---|---|---|---|
| `house-ex.com` (ours) | `/projects/<slug>` | client-rendered: use `document.body.innerText` | `admin.house-ex.com//storage/<id>/…webp`; the project's photos are the first N matching the page's «إظهار N صور» (later ones are icons / other projects). Its footer shows `+90 551 900 66 00`: ignore it, the carousel keeps `+90 551 4000 200`. |
| `emlakplatform.com.tr` | `/ar/projects/<slug>`. The `/ar/portal/projects/<slug>` link needs a login: switch to the public one | `get_page_text` works | `/proj_imgs/…` (gallery) and the `/uploads/` cover image. Other `/uploads/` images are similar projects; `/icons/` are distance icons. Marked «صور نموذجية» (renders). |
| any other site | – | browser text | gallery `<img>` / HTML image URLs; skip logos, icons, avatars, other listings |

If a page needs a login, ask Mohamad to sign in in the browser pane himself, or to send the text. Never type a password.

Brand: colours/logo/fonts follow the `housex-brand` skill. Logos live in `carousel/assets/`.
`carousel/projects/vadi-premium/` is the reference example.

## Open decisions (ask Mohamad before changing)

- ~~Footer phone~~ decided 2026-10-10: always `+90 551 4000 200` (not the brand skill's `+90 551 900 6600`) unless Mohamad says otherwise.
- ~~CTA badge contrast~~ decided 2026-10-10: slide-5 CTA badge is navy `#26247b` text on gold `#ffca05`.
- Only one template exists (project launch). Proposed next: "market news" and "Turkish citizenship" templates on the same project.json pipeline.

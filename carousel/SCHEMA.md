# HOUSE X Carousel — project.json

One file describes a full 5-slide carousel. `render.mjs` turns it into PNGs;
`builder.html` can import it (📥 مشروع) or load it via `builder.html?project=path.json`
for manual fine-tuning, and export it back (📤 حفظ).

```
carousel/projects/<slug>/
  project.json
  images/            ← photos for this project (jpg/png/webp)
  out/               ← generated, not committed
```

## Top level

| key | value |
|---|---|
| `name` | slug, used for file names (`vadi-premium`) |
| `source` | optional: the page the copy came from, `"<url> (<date>)"` — not shown on the slides |
| `format` | `post` (1080×1350) or `story` (1080×1920) |
| `theme.bg` | background photo for every slide, path relative to project.json, URL, or data URI. Omit → brand navy field |
| `theme.collage` | cover collage photo (slide 1) |
| `theme.collageLayout` | `top-two` · `full` · `three-h` · `three-v` · `grid4` · `left-big` · `right-big` |
| `theme.overlay` | `{ "color": "#ffffff", "opacity": 0-95 }` wash over the photo |
| `theme.logo` | `fullcolor` · `white-colorx` · `all-white` · `navy-mono` · path · `null` (hidden) |
| `theme.logoSize` | logo width at preview scale; **95** keeps the Arabic line legible (brand minimum) |
| `contact` | `{ phone, web, show, align: left/center/right, color, size }` |
| `slides.s0` … `slides.s4` | per-slide content, below |

Any slide may add `look` to override the theme on that slide only:
`{ "bg", "overlay", "logo", "footerColor" }` — e.g. dark photo → `white-colorx` logo + `#ffffff` footer.

Any slide may also override style keys from the builder (`szTAr`, `cTAr`, `fTAr`, `cardOp`, `fx` …).
Leave them out unless there is a reason; `render.mjs` auto-shrinks text that overflows.

## Slide content

`*word*` renders as the accent colour (red) — use it for 1-2 numbers or power words per line, never whole sentences.

The slide text is polished automatically (builder and renderer):
- digits become Latin (`١٥` → `15`), so write Latin digits in the first place;
- number groups (`2+1`, `2-5`, `24/7`) are kept as one left-to-right unit, so they never flip to `1+2` or split across lines;
- the last two words of a line are joined, so no single word sits alone on the last line.

| slide | keys | length guide (Arabic chars) |
|---|---|---|
| **s0 Cover** | `tAr` project name · `tEn` Latin name · `sl1`, `sl2` two-line hook | tAr ≤ 18 · tEn ≤ 22 · sl ≤ 45 each |
| **s1 Units** | `h` headline lead-in · `hR` hero number (`48,200 م²`) · `u1` `u2` `u3` bullets · `cl` closer | h ≤ 22 · u ≤ 34 · cl ≤ 32 |
| **s2 Location** | `title` · `t1..t4` time (`15 دقيقة`) · `d1..d4` destination · `cl` closer | title ≤ 32 · t ≤ 9 · d ≤ 24 · cl ≤ 45 |
| **s3 Amenities** | `title` · `items` array (6-10) | title ≤ 32 · item ≤ 22 |
| **s4 CTA** | `nameEn` · `slogan` · `desc` · `ctaBtn` · `ctaText` | slogan ≤ 26 · desc ≤ 110 · ctaText ≤ 40 |

Lengths are guides, not hard limits: stay inside them and slides render at full size;
go over and the renderer shrinks the slide's text until it fits (reported in `out/fit-report.json`).

## Render

```bash
npm install                                    # once (playwright)
node carousel/render.mjs carousel/projects/<slug>/project.json            # post
node carousel/render.mjs carousel/projects/<slug>/project.json --story    # story
```

Before rendering, the project is checked. Errors stop the render (exit code 1): a missing slide or text key
(it would otherwise show the builder's Vadi Premium copy), fewer than 6 or more than 10 amenities, an image file
that does not exist. Warnings are printed and the render continues: text over its length guide, no photos
(empty navy cover collage), a footer phone other than `+90 551 4000 200`. `--check` runs only this step.

Exit code 2 = a slide still has a layout issue after auto-fit; the warning names the element
(`el_d3`, `el_tAr:logo`, `el_descBox:overlaps-cta` …). Shorten that text and re-render.

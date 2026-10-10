#!/usr/bin/env node
// Renders a HOUSE X weekly-tips carousel («قبل ما توقّع») to PNG slides.
//
//   node carousel/tips/render-tips.mjs carousel/tips/<slug>/tip.json [--post | --story] [--check] [--no-export]
//
// Same pipeline as the project carousels (render.mjs): feed 1080×1350 + story
// 1080×1920, <slug>-social.docx from social.json, latest set copied to
// ~/Documents/HOUSE X Carousels/<slug>/ (Feed/, Story/, the .docx).
// Tips are not projects, so they stay out of «سجل المشاريع.xlsx».
// Exit 1 = check errors; exit 2 = a slide still overflows after auto-fit.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { buildSocialDoc, checkSocial } from '../social-doc.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const tipPath = args.find(a => !a.startsWith('--'));
if (!tipPath) {
  console.error('usage: node carousel/tips/render-tips.mjs <tip.json> [--post | --story] [--check] [--no-export]');
  process.exit(1);
}
const tipDir = path.dirname(path.resolve(tipPath));
const tip = JSON.parse(fs.readFileSync(tipPath, 'utf8'));
const slug = tip.name || path.basename(tipDir);
const FORMATS = args.includes('--story') ? ['story'] : args.includes('--post') ? ['post'] : ['post', 'story'];
const socialPath = path.join(tipDir, 'social.json');
const social = fs.existsSync(socialPath) ? JSON.parse(fs.readFileSync(socialPath, 'utf8')) : null;

// ── Check ────────────────────────────────────────────
const HOUSE_PHONE = '+90 551 4000 200';
const TYPES = ['cover', 'points', 'compare', 'share', 'cta'];
const MONEY = /سعر|أسعار|اسعار|دفع|قسط|أقساط|اقساط|تقسيط|خصم|ليرة|دولار|يورو|\$|€|₺|\bTRY\b|\bUSD\b|\bEUR\b/;
{
  const errors = [], warnings = [];
  const slides = tip.slides || [];
  if (slides.length < 4 || slides.length > 8) errors.push(`${slides.length} شرائح، المطلوب 4 إلى 8`);
  if (slides[0]?.type !== 'cover') errors.push('الشريحة الأولى يجب أن تكون cover');
  if (slides.at(-1)?.type !== 'cta') errors.push('الشريحة الأخيرة يجب أن تكون cta');
  if (!slides.some(s => s.type === 'share')) warnings.push('لا توجد شريحة مشاركة (share) قبل الدعوة');
  slides.forEach((s, i) => {
    const at = `s${i + 1} (${s.type})`;
    if (!TYPES.includes(s.type)) errors.push(`${at}: نوع غير معروف`);
    if (!s.title) errors.push(`${at}: title فارغ`);
    if (s.type === 'points' && !(s.items || []).length) errors.push(`${at}: items فارغة`);
    if (s.type === 'compare' && !(s.pairs || []).length) errors.push(`${at}: pairs فارغة`);
    if (s.type === 'cta') {
      const text = [s.title, ...(s.lines || []), s.button, s.ctaText].join(' ');
      const m = text.match(MONEY);
      if (m) errors.push(`${at}: لا أسعار ولا دفعات في الشريحة الأخيرة («${m[0]}»)`);
    }
  });
  // The cover's number must match the content ("5 أسئلة" → 5 items).
  const n = (slides[0]?.title + ' ' + (slides[0]?.sub || '')).match(/\b(\d+)\b/);
  // Numbered items only: badges such as «!» or «✓» are extras, not part of the count.
  const count = slides.reduce((a, s) => a + (s.items || []).filter(it => typeof it.n === 'number').length + (s.pairs || []).length, 0);
  if (n && +n[1] !== count && count) warnings.push(`الغلاف يذكر ${n[1]} والمحتوى فيه ${count} بنود`);
  if (tip.contact?.phone !== HOUSE_PHONE) warnings.push(`رقم التذييل ${tip.contact?.phone} يختلف عن ${HOUSE_PHONE}`);
  if (!social) warnings.push('لا يوجد social.json');
  else errors.push(...checkSocial(social));
  warnings.forEach(w => console.warn(`⚠ ${w}`));
  errors.forEach(e => console.error(`✖ ${e}`));
  console.log(`check: ${errors.length} errors, ${warnings.length} warnings`);
  if (errors.length) process.exit(1);
  if (args.includes('--check')) process.exit(0);
}

// Photos (slide bg, cover phone, compare pairs) are paths relative to tip.json,
// inlined as data URIs so the render never depends on remote hosts.
const MIME = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp' };
const inline = file => `data:${MIME[path.extname(file).toLowerCase()] || 'application/octet-stream'};base64,${fs.readFileSync(file).toString('base64')}`;
const photo = ref => {
  if (!ref || /^data:/.test(ref)) return ref;
  const file = path.resolve(tipDir, ref);
  if (!fs.existsSync(file)) { console.error(`✖ الصورة غير موجودة: ${ref}`); process.exit(1); }
  return inline(file);
};
const logoFile = path.join(here, '..', 'assets', `logo-${tip.logo || 'white-colorx'}.png`);
const data = {
  ...tip,
  logo: inline(logoFile),
  slides: tip.slides.map(s => ({
    ...s,
    bg: photo(s.bg),
    phonePhoto: photo(s.phonePhoto),
    pairs: s.pairs && s.pairs.map(p => ({ ...p, photo: photo(p.photo) })),
  })),
};

const outDir = path.join(tipDir, 'out');
fs.mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch()
  .catch(() => chromium.launch({ channel: 'chrome' }))
  .catch(() => chromium.launch({ channel: 'msedge' }));

const report = [], files = [];
for (const format of FORMATS) {
  const h = format === 'story' ? 1920 : 1350;
  const page = await browser.newPage({ viewport: { width: 1080, height: h } });
  page.on('pageerror', e => console.warn('page error:', e.message));
  await page.goto(pathToFileURL(path.join(here, 'tips.html')).href, { waitUntil: 'networkidle' });
  for (let i = 0; i < tip.slides.length; i++) {
    const fit = await page.evaluate(([d, n, f]) => window.TIPS.fit(d, n, f), [data, i, format]);
    await page.waitForTimeout(350);   // let the inlined photos decode
    const file = path.join(outDir, `${slug}-s${i + 1}${format === 'story' ? '-story' : ''}.png`);
    await page.screenshot({ path: file, animations: 'disabled', clip: { x: 0, y: 0, width: 1080, height: h } });
    files.push({ format, file });
    report.push({ format, slide: i + 1, ...fit });
    const flag = fit.remaining.length ? `  ⚠ ${fit.remaining.join(', ')}` : fit.k < 1 ? `  (k=${fit.k})` : '';
    console.log(`${format === 'story' ? 'story' : 'feed '} s${i + 1} → ${path.relative(process.cwd(), file)}${flag}`);
  }
  await page.close();
}
fs.writeFileSync(path.join(outDir, 'fit-report.json'), JSON.stringify(report, null, 2));
await browser.close();

const docFile = path.join(outDir, `${slug}-social.docx`);
if (social) {
  // social-doc.mjs titles the file from slides.s0.tAr.
  await buildSocialDoc({ project: { name: slug, slides: { s0: { tAr: tip.titleAr || slug } } }, social, file: docFile, date: new Date().toISOString().slice(0, 10) });
  console.log(`social → ${path.relative(process.cwd(), docFile)}`);
}

if (!process.env.CI && !args.includes('--no-export')) {
  const root = process.env.HOUSEX_EXPORT_DIR || path.join(os.homedir(), 'Documents', 'HOUSE X Carousels');
  const dest = path.join(root, slug);
  for (const format of FORMATS) {
    const dir = path.join(dest, format === 'story' ? 'Story' : 'Feed');
    fs.rmSync(dir, { recursive: true, force: true });
    fs.mkdirSync(dir, { recursive: true });
    for (const f of files.filter(x => x.format === format)) fs.copyFileSync(f.file, path.join(dir, path.basename(f.file)));
  }
  if (social) fs.copyFileSync(docFile, path.join(dest, path.basename(docFile)));
  console.log(`export → ${dest}`);
}
if (report.some(r => r.remaining.length)) process.exitCode = 2;

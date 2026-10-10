#!/usr/bin/env node
// Renders a HOUSE X carousel project to PNG slides.
//
//   node carousel/render.mjs carousel/projects/<slug>/project.json [--story] [--no-fit]
//
// Output: carousel/projects/<slug>/out/<slug>-s1.png … s5.png (1080×1350, or
// 1080×1920 with --story) plus fit-report.json. Local image paths in the
// project are resolved relative to project.json and inlined, so the render
// never depends on remote image hosts.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const projectPath = args.find(a => !a.startsWith('--'));
if (!projectPath) {
  console.error('usage: node carousel/render.mjs <project.json> [--story] [--no-fit]');
  process.exit(1);
}
const projectDir = path.dirname(path.resolve(projectPath));
const project = JSON.parse(fs.readFileSync(projectPath, 'utf8'));
if (args.includes('--story')) project.format = 'story';
const slug = project.name || path.basename(projectDir);

const MIME = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.svg': 'image/svg+xml' };
const LOGOS = ['fullcolor', 'white-colorx', 'all-white', 'navy-mono'];

function inline(ref) {
  if (!ref || /^(data:|https?:)/.test(ref)) return ref;
  const file = LOGOS.includes(ref)
    ? path.join(here, 'assets', `logo-${ref}.png`)
    : path.resolve(projectDir, ref);
  if (!fs.existsSync(file)) throw new Error(`image not found: ${ref} (${file})`);
  const mime = MIME[path.extname(file).toLowerCase()] || 'application/octet-stream';
  return `data:${mime};base64,${fs.readFileSync(file).toString('base64')}`;
}

const t = project.theme || {};
for (const k of ['bg', 'collage', 'logo']) if (t[k]) t[k] = inline(t[k]);
for (const s of Object.values(project.slides || {})) {
  if (!s.look) continue;
  for (const k of ['bg', 'logo']) if (s.look[k]) s.look[k] = inline(s.look[k]);
}
// No photo supplied: fall back to a brand navy field instead of a remote stock image.
const NAVY = 'data:image/svg+xml;base64,' + Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1350"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#26247b"/><stop offset="1" stop-color="#121140"/></linearGradient></defs><rect width="100%" height="100%" fill="url(#g)"/></svg>'
).toString('base64');
t.bg ||= NAVY;
t.collage ||= t.bg;
project.theme = t;

const outDir = path.join(projectDir, 'out');
fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch(
  fs.existsSync('/opt/pw-browsers/chromium') ? { executablePath: '/opt/pw-browsers/chromium' } : {}
).catch(() => chromium.launch());

const fmt = project.format === 'story' ? { w: 280, h: 280 * 16 / 9 } : { w: 378, h: 472.5 };
const out = { w: 1080, h: project.format === 'story' ? 1920 : 1350 };
const page = await browser.newPage({ viewport: { width: 1200, height: 2000 } });
page.on('pageerror', e => console.warn('page error:', e.message));
await page.goto(pathToFileURL(path.join(here, 'builder.html')).href, { waitUntil: 'load' });
await page.evaluate(() => document.fonts.ready);
await page.evaluate(p => window.HX.loadProject(p), project);
// Layout stays at preview size (so fit checks match the builder); the
// capture is the slide scaled up to whole export pixels from the page origin.
const shoot = async file => {
  await page.evaluate(k => {
    const s = document.getElementById('slide');
    s.dataset.prev = s.getAttribute('style');
    Object.assign(s.style, { position: 'fixed', top: '0', left: '0', zIndex: 9999, borderRadius: '0', boxShadow: 'none', transformOrigin: '0 0', transform: `scale(${k})` });
  }, out.w / fmt.w);
  await page.screenshot({ path: file, animations: 'disabled', clip: { x: 0, y: 0, width: out.w, height: out.h } });
  await page.evaluate(() => { const s = document.getElementById('slide'); s.setAttribute('style', s.dataset.prev); });
};

const report = [];
for (let i = 0; i < 5; i++) {
  const fit = args.includes('--no-fit')
    ? { slide: i, steps: 0, remaining: await page.evaluate(n => { window.HX.goSlide(n); return window.HX.fitIssues(); }, i) }
    : await page.evaluate(n => window.HX.fitSlide(n), i);
  await page.evaluate(() => Promise.all([...document.images].map(im => im.complete ? 0 : new Promise(r => { im.onload = im.onerror = r; }))));
  await page.waitForTimeout(150);
  const file = path.join(outDir, `${slug}-s${i + 1}${project.format === 'story' ? '-story' : ''}.png`);
  await shoot(file);
  report.push({ ...fit, file: path.relative(process.cwd(), file) });
  const flag = fit.remaining.length ? `  ⚠ ${fit.remaining.join(', ')}` : fit.steps ? `  (shrunk ${fit.steps}×)` : '';
  console.log(`s${i + 1} → ${path.relative(process.cwd(), file)}${flag}`);
}
fs.writeFileSync(path.join(outDir, 'fit-report.json'), JSON.stringify(report, null, 2));
await browser.close();
if (report.some(r => r.remaining.length)) process.exitCode = 2;

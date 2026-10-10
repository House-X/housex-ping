#!/usr/bin/env node
// Downloads project photos into carousel/projects/<slug>/images/.
//
//   node carousel/fetch-images.mjs <slug> <url> [<url> …]
//
// Files are saved in the order given as 01.webp, 02.jpg … (existing numbers are
// skipped, so it can be re-run to add more). Only image responses up to 15 MB
// are kept. Prints the saved paths for project.json.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const [slug, ...urls] = process.argv.slice(2);
if (!slug || !urls.length || !/^[a-z0-9-]+$/.test(slug)) {
  console.error('usage: node carousel/fetch-images.mjs <slug> <url> [<url> …]   (slug: a-z 0-9 -)');
  process.exit(1);
}
const dir = path.join(here, 'projects', slug, 'images');
fs.mkdirSync(dir, { recursive: true });

const EXT = { 'image/webp': '.webp', 'image/jpeg': '.jpg', 'image/png': '.png' };
const MAX = 15 * 1024 * 1024;
let n = fs.readdirSync(dir).map(f => parseInt(f, 10)).filter(Number.isFinite).reduce((a, b) => Math.max(a, b), 0);
let failed = 0;

for (const url of urls) {
  try {
    if (!/^https:\/\//.test(url)) throw new Error('only https URLs');
    const res = await fetch(url, { redirect: 'follow' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const type = (res.headers.get('content-type') || '').split(';')[0].trim();
    if (!EXT[type]) throw new Error(`not an image (${type || 'no content-type'})`);
    const buf = Buffer.from(await res.arrayBuffer());
    if (buf.length > MAX) throw new Error(`too large (${Math.round(buf.length / 1048576)} MB)`);
    const file = path.join(dir, String(++n).padStart(2, '0') + EXT[type]);
    fs.writeFileSync(file, buf);
    console.log(`images/${path.basename(file)}  ${Math.round(buf.length / 1024)} KB  ← ${url}`);
  } catch (e) {
    failed++;
    console.error(`✖ ${url}: ${e.message}`);
  }
}
if (failed) process.exitCode = 1;

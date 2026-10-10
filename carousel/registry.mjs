// Project registry: every carousel project with its creation date, so the same
// project is never built twice.
//   · findDuplicates() — used by render.mjs's pre-render check
//   · writeRegistry()  — «سجل المشاريع.xlsx» in the export folder, after each render
import fs from 'node:fs';
import path from 'node:path';
import ExcelJS from 'exceljs';

export const REGISTRY_FILE = 'سجل المشاريع.xlsx';

export function listProjects(projectsDir) {
  return fs.readdirSync(projectsDir, { withFileTypes: true })
    .filter(d => d.isDirectory() && fs.existsSync(path.join(projectsDir, d.name, 'project.json')))
    .map(d => {
      const p = JSON.parse(fs.readFileSync(path.join(projectsDir, d.name, 'project.json'), 'utf8'));
      const s0 = (p.slides && p.slides.s0) || {};
      return {
        slug: p.name || d.name,
        nameAr: s0.tAr || '',
        nameEn: (s0.tEn || (p.slides && p.slides.s4 && p.slides.s4.nameEn) || '').replace(/[()]/g, '').trim(),
        location: p.location || '',
        created: p.created || '',
        source: (p.source || '').replace(/\s*\(\d{4}-\d{2}-\d{2}\)\s*$/, ''),
      };
    });
}

// Same project = same Arabic name, same Latin name or same source page.
const normName = s => s.toLowerCase().replace(/[()\s\-_.]/g, '').replace(/[أإآ]/g, 'ا').replace(/ة/g, 'ه').replace(/ى/g, 'ي');
const normUrl = u => u.toLowerCase().replace(/^https?:\/\/(www\.)?/, '').replace(/[?#].*$/, '').replace(/\/portal\//, '/').replace(/\/+$/, '');

export function findDuplicates(projectsDir, slug) {
  const all = listProjects(projectsDir), me = all.find(p => p.slug === slug);
  if (!me) return [];
  const out = [];
  for (const o of all) {
    if (o.slug === slug) continue;
    if (me.nameAr && normName(me.nameAr) === normName(o.nameAr)) out.push(`الاسم «${me.nameAr}» موجود في المشروع ${o.slug} (${o.created || 'بلا تاريخ'})`);
    else if (me.nameEn && normName(me.nameEn) === normName(o.nameEn)) out.push(`الاسم ${me.nameEn} موجود في المشروع ${o.slug} (${o.created || 'بلا تاريخ'})`);
    else if (me.source && o.source && normUrl(me.source) === normUrl(o.source)) out.push(`نفس رابط المصدر موجود في المشروع ${o.slug} (${o.created || 'بلا تاريخ'})`);
  }
  return out;
}

// Last render date = when the project's export folder was last refilled.
function lastRendered(root, slug) {
  const times = ['Feed', 'Story'].map(d => path.join(root, slug, d)).filter(fs.existsSync).map(d => fs.statSync(d).mtime);
  if (!times.length) return '';
  const t = new Date(Math.max(...times));
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
}

export async function writeRegistry(projectsDir, root) {
  const rows = listProjects(projectsDir).sort((a, b) => (a.created || '9999').localeCompare(b.created || '9999') || a.slug.localeCompare(b.slug));
  const wb = new ExcelJS.Workbook();
  wb.creator = 'HOUSE X Real Estate';
  const ws = wb.addWorksheet('المشاريع', { views: [{ rightToLeft: true, state: 'frozen', ySplit: 1 }] });
  ws.columns = [
    { header: '#', key: 'n', width: 5 },
    { header: 'اسم المشروع', key: 'nameAr', width: 24 },
    { header: 'الاسم بالإنجليزية', key: 'nameEn', width: 22 },
    { header: 'المنطقة', key: 'location', width: 24 },
    { header: 'تاريخ الإنشاء', key: 'created', width: 15 },
    { header: 'آخر توليد', key: 'last', width: 15 },
    { header: 'الرمز (المجلد)', key: 'slug', width: 20 },
    { header: 'رابط المصدر', key: 'source', width: 62 },
  ];
  rows.forEach((r, i) => {
    const row = ws.addRow({ n: i + 1, ...r, last: lastRendered(root, r.slug) });
    if (fs.existsSync(path.join(root, r.slug))) row.getCell('slug').value = { text: r.slug, hyperlink: encodeURI(`./${r.slug}/`) };
    if (r.source) row.getCell('source').value = { text: r.source, hyperlink: r.source };
  });
  const font = { name: 'Arial', size: 11, color: { argb: 'FF1A1A3E' } };
  ws.eachRow((row, n) => row.eachCell({ includeEmpty: true }, c => {
    c.font = n === 1 ? { ...font, bold: true, color: { argb: 'FFFFFFFF' } } : (c.value && c.value.hyperlink ? { ...font, underline: true, color: { argb: 'FF26247B' } } : font);
    // Latin columns (slug, URL) read left-to-right; the rest right-to-left.
    const latin = n > 1 && (c.col === 7 || c.col === 8);
    c.alignment = { vertical: 'middle', horizontal: c.col === 1 ? 'center' : latin ? 'left' : 'right', readingOrder: latin ? 'ltr' : 'rtl' };
    if (n === 1) c.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF26247B' } };
    c.border = { bottom: { style: 'thin', color: { argb: 'FFD9D9E3' } } };
  }));
  ws.getRow(1).height = 24;
  ws.autoFilter = { from: 'A1', to: 'H1' };
  const file = path.join(root, REGISTRY_FILE);
  try {
    fs.writeFileSync(file, Buffer.from(await wb.xlsx.writeBuffer()));
    return { file, count: rows.length };
  } catch (e) {
    return { file, count: rows.length, error: e.code === 'EBUSY' || e.code === 'EPERM' ? 'الملف مفتوح في Excel — أغلقه وأعد التوليد لتحديث السجل' : e.message };
  }
}

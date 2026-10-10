// Builds the project's social-media copy (social.json) into an RTL Word file.
//
// social.json:
//   { "instagram": { "caption": ["paragraph", …], "hashtags": ["#…", …] },
//     "facebook":  { "post":    ["paragraph", …], "hashtags": ["#…", …] } }
import fs from 'node:fs';
import { Document, Packer, Paragraph, TextRun, AlignmentType, BorderStyle } from 'docx';

const NAVY = '26247B', RED = 'EC1C24', INK = '1A1A3E', GREY = '6B6B80';
const FONT = { ascii: 'Arial', hAnsi: 'Arial', cs: 'Arial' };

// Phone numbers and codes like O-7 flip inside an Arabic paragraph (+90 551 … shows
// as … 551 90+). Wrapping them in LRE…PDF keeps them left-to-right in Word and,
// because the marks travel with copy/paste, in the Instagram / Facebook post too.
const LTR = /\+?\d+(?: \d+)+|[A-Za-z0-9]+(?:[-+.\/:][A-Za-z0-9]+)+/g;
const ltr = t => t.replace(LTR, m => (/[A-Za-z]/.test(m) || / /.test(m) || /^\+/.test(m) ? `‪${m}‬` : m));
const run = (text, o = {}) => new TextRun({
  text: ltr(text), font: FONT, rightToLeft: true,
  size: o.size || 24, sizeComplexScript: o.size || 24,
  bold: !!o.bold, boldComplexScript: !!o.bold, color: o.color || INK,
});
const para = (text, o = {}) => new Paragraph({
  bidirectional: true, alignment: AlignmentType.RIGHT,
  spacing: { after: o.after ?? 160, line: 340 },
  border: o.rule ? { bottom: { style: BorderStyle.SINGLE, size: 8, color: o.rule, space: 4 } } : undefined,
  children: [run(text, o)],
});

function section(title, accent, paragraphs, hashtags) {
  const out = [para(title, { size: 30, bold: true, color: accent, rule: accent, after: 200 })];
  for (const p of paragraphs || []) out.push(para(p));
  if (hashtags && hashtags.length) out.push(para(hashtags.join(' '), { color: NAVY, size: 22, after: 360 }));
  return out;
}

export function checkSocial(social) {
  const errors = [];
  if (!social.instagram || !Array.isArray(social.instagram.caption) || !social.instagram.caption.length) errors.push('social.json: instagram.caption فارغ');
  if (!social.facebook || !Array.isArray(social.facebook.post) || !social.facebook.post.length) errors.push('social.json: facebook.post فارغ');
  return errors;
}

export async function buildSocialDoc({ project, social, file, date }) {
  const title = project.slides?.s0?.tAr || project.name;
  const latin = project.slides?.s4?.nameEn || '';
  const doc = new Document({
    creator: 'HOUSE X Real Estate',
    title: `${title} — منشورات`,
    sections: [{
      properties: { page: { margin: { top: 1200, bottom: 1200, left: 1200, right: 1200 } } },
      children: [
        para(`${title}${latin ? ` · ${latin}` : ''}`, { size: 36, bold: true, color: NAVY, after: 60 }),
        para(`منشورات انستغرام وفيسبوك — HOUSE X · ${date}`, { size: 20, color: GREY, after: 360 }),
        ...section('انستغرام', RED, social.instagram.caption, social.instagram.hashtags),
        ...section('فيسبوك', NAVY, social.facebook.post, social.facebook.hashtags),
      ],
    }],
  });
  fs.writeFileSync(file, await Packer.toBuffer(doc));
}

// Offline check for plan v0.5 D1 (raw Windows paths survive marked). Run: node tests/js/paths_test.mjs
import { marked } from '../../gui/vendor/marked/marked.esm.js';
import { rewriteFileLinks, findPaths } from '../../gui/lib/paths.js';

let fail = 0;
const ok = (name, cond, detail = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : ' :: ' + detail}`); if (!cond) fail++; };
const render = (s) => marked.parse(rewriteFileLinks(s), { async: false });
const decodeHrefs = (html) => [...html.matchAll(/href="#neo-file:([^"]+)"/g)].map((m) => decodeURIComponent(m[1]));
const text = (html) => html.replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&#39;/g, "'").replace(/&quot;/g, '"');

const cases = [
  'C:\\Users\\alice.smith\\_neo-home',
  'C:\\work\\a\\.hidden\\-dash\\x_y',
  'D:\\Temp\\__init__.py',
];
for (const p of cases) {
  const html = render(`Your home is ${p}. Done.`);
  ok(`href keeps ${p}`, decodeHrefs(html).includes(p.replace(/\.$/, '')), html);
  ok(`text keeps ${p}`, text(html).includes(p), html);
}
ok('PATH_RE matches underscore segment', findPaths('see C:\\Users\\k\\_x-home now')[0] === 'C:\\Users\\k\\_x-home');
const glob = render('Logs: C:\\logs\\_a\\*.txt here');
ok('glob text keeps backslash before *', text(glob).includes('C:\\logs\\_a\\*.txt'), glob);
const spaced = render('Open `C:\\Program Files\\_App\\a b.txt` please');
ok('inline code untouched', spaced.includes('<code>C:\\Program Files\\_App\\a b.txt</code>'), spaced);
const fenced = render('```\nC:\\Users\\k\\_x\n```');
ok('fenced code untouched', fenced.includes('C:\\Users\\k\\_x') && !fenced.includes('neo-file'), fenced);
const linked = render('[home](C:\\Users\\k\\_x)');
ok('explicit link kept as one link', decodeHrefs(linked).join() === 'C:\\Users\\k\\_x' && text(linked).trim() === 'home', linked);
const url = render('see https://h.example/C:\\x\\_y');
ok('path inside a URL not rewritten', !url.includes('neo-file'), url);
const trail = render('Saved to C:\\out\\_r.txt.');
ok('trailing period outside link', decodeHrefs(trail).join() === 'C:\\out\\_r.txt' && text(trail).includes('_r.txt.'), trail);
const md = render('**bold** and C:\\a\\_b and _em_');
ok('rest of markdown still renders', md.includes('<strong>bold</strong>') && md.includes('<em>em</em>'), md);
console.log(fail ? `${fail} FAILED` : 'ALL PASS');
process.exit(fail ? 1 : 0);


// Generates Starlight content from the canonical Markdown in /docs, keeping
// /docs as the single source of truth (its files stay pristine and their
// GitHub-relative links keep working). Run automatically before dev/build.
//
// For each docs/NN-name.md it: derives the title from the leading H1, injects
// Starlight frontmatter, drops the duplicate H1, and rewrites intra-doc
// `NN-name.md` links to site routes under the configured base.
import { readdirSync, readFileSync, writeFileSync, rmSync, mkdirSync, existsSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const REPO = join(here, '..', '..');
const DOCS_SRC = join(REPO, 'docs');
const OUT = join(here, '..', 'src', 'content', 'docs');
export const BASE = '/azure-emulators/docs/';

// The parity map is the one doc without a reading-order number: it is a living
// reference rather than a chapter, and its URL is just /parity/.
const PARITY_RE = /(^|\/)parity\.md$/;
// Docs are `NN-name.md` chapters, plus the un-numbered parity map.
const DOC_RE = /^(\d{2}-.*|parity)\.md$/;

// Rewrite `](./|docs/ NN-slug.md#anchor)` → `](/azure-emulators/docs/NN-slug/#anchor)`.
const LINK_RE = /\]\((?:\.\/|docs\/)?(\d{2}-[a-z0-9-]+|parity)\.md(#[^)]*)?\)/g;
// Repo-relative links (`../docker-compose.yml`) are correct on GitHub, where /docs sits one
// level under the repo root — but they are dead on the site, whose pages are
// served from flat `/<base>/<slug>/` routes with nothing above them. Rewriting
// to an absolute GitHub URL is what keeps ONE source of truth working in both
// renderings, which is this script's whole premise; the alternative is editing
// /docs into something that no longer resolves on GitHub.
//
// `tree` vs `blob` is decided from what the path actually is on disk rather
// than guessed from a trailing slash, and a path that resolves to nothing is
// reported rather than silently linked into a 404.
const REPO_URL = 'https://github.com/calvinchengx/azure-emulators';
const REPO_LINK_RE = /\]\(\.\.\/([^)#]+)(#[^)]*)?\)/g;
function rewriteRepoLinks(md, where) {
  return md.replace(REPO_LINK_RE, (_m, path, anchor) => {
    const clean = path.replace(/\/+$/, '');
    const target = join(REPO, clean);
    const exists = existsSync(target);
    if (!exists) {
      console.warn(`sync-docs: WARNING ${where}: ../${path} matches nothing in the repo`);
    }
    const kind = exists && statSync(target).isDirectory() ? 'tree' : 'blob';
    return `](${REPO_URL}/${kind}/main/${clean}${anchor ?? ''})`;
  });
}

function rewriteLinks(md, where = 'docs') {
  const sitewide = md.replace(LINK_RE, (_m, slug, anchor) => `](${BASE}${slug}/${anchor ?? ''})`);
  return rewriteRepoLinks(sitewide, where);
}

// "06 — Secrets" → "Secrets".
function cleanTitle(h1) {
  return h1.replace(/^\d+[a-z]?\s*[—:-]\s*/i, '').trim();
}

// Backslashes must be escaped before quotes, or a title ending in one would
// escape the closing quote and produce unparseable frontmatter.
function yamlEscape(s) {
  return '"' + s.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
}

// Strip the leading H1 (Starlight renders the frontmatter title) and rewrite
// intra-doc links. Shared with the parity snapshot generator so historical
// snapshots convert identically.
// The page's own meta description, taken from the first real paragraph.
//
// WHY. Starlight falls back to the SITE description when a page declares none,
// so every page of a site shipped the same `<meta name="description">` --
// checked on three pages of this site and they were byte-identical. Google
// discards duplicate descriptions and writes its own snippet, so 300+ pages
// across this family were competing with one sentence between them.
//
// FIRST PARAGRAPH, not a summary. It is the one sentence the author already
// wrote to introduce the page, and deriving it means it cannot go stale. Skips
// headings, code fences, tables, quotes, images, lists and HTML, which are all
// things that read badly as a search snippet.
//
// Absent rather than empty when nothing suitable is found: Starlight then falls
// back to the site description, which is the old behaviour and no worse.
function description(raw) {
  const lines = raw.split('\n');
  let inFence = false;
  const para = [];
  for (const line of lines) {
    const t = line.trim();
    if (/^(```|~~~)/.test(t)) { inFence = !inFence; continue; }
    if (inFence) continue;
    if (para.length === 0) {
      if (!t) continue;
      if (/^(#|>|\||-|\*|\d+\.|!\[|<)/.test(t)) continue;
      para.push(t);
    } else {
      if (!t || /^(#|>|\||```|~~~)/.test(t)) break;
      para.push(t);
    }
  }
  if (para.length === 0) return null;
  // Markdown emphasis, links and code marks read as noise in a snippet.
  let text = para
    .join(' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[`*_]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  // 25, not 40. "Seven services, one discipline." is 30 characters and is a
  // better description than the site-wide sentence it would otherwise inherit:
  // distinctive and short beats generic and long, for a snippet.
  if (text.length < 25) return null;
  // Search engines truncate around 160; cut on a sentence, else on a word.
  if (text.length > 160) {
    const stop = text.lastIndexOf('. ', 160);
    text = stop > 80 ? text.slice(0, stop + 1)
                     : text.slice(0, text.lastIndexOf(' ', 157)) + '\u2026';
  }
  return text;
}

function convertBody(raw, where = 'docs') {
  const lines = raw.split('\n');
  const h1Index = lines.findIndex((l) => /^#\s+/.test(l));
  if (h1Index >= 0) {
    lines.splice(h1Index, lines[h1Index + 1]?.trim() === '' ? 2 : 1);
  }
  return rewriteLinks(lines.join('\n').replace(/^\n+/, ''), where);
}


const entries = [];

function convert(name) {
  const raw = readFileSync(join(DOCS_SRC, name), 'utf8');
  const h1 = raw.split('\n').find((l) => /^#\s+/.test(l));
  const title = h1 ? cleanTitle(h1.replace(/^#\s+/, '')) : name.replace(/\.md$/, '');
  let body = convertBody(raw, name);
  // Point "Edit this page" at the real source in /docs (the generated copy
  // under src/content/docs/ is git-ignored), not Starlight's default path.
  const editUrl = `${REPO_URL}/edit/main/docs/${name}`;
  const desc = description(raw);
  entries.push({ slug: name.replace(/\.md$/, ''), title, desc });
  const frontmatter =
    `---\ntitle: ${yamlEscape(title)}\n` +
    (desc ? `description: ${yamlEscape(desc)}\n` : '') +
    `editUrl: ${yamlEscape(editUrl)}\n---\n\n`;
  return frontmatter + body;
}

// NO writeIndex() ANY MORE, and this note is here so its absence reads as a
// decision rather than an omission.
//
// The docs root is `website/src/pages/index.astro` -- the landing page, served
// at the site root AND at the docs base from one build output. An index.md
// here would have claimed that second route. Its curated chapter list moved
// onto that page, under #docs.


// ---------------------------------------------------------------------------
// llms.txt for this site.
//
// A PROPOSED convention (llmstxt.org), not a standard: a markdown file at a
// site root giving a model a short, link-dense map of what the site holds, so
// a crawler need not infer the shape from HTML. No major provider has
// committed to consuming it. It is cheap and cannot hurt; it is not a
// substitute for the per-page descriptions above, which affect search today.
//
// GENERATED FROM THE SAME PASS that writes the pages, so the title, the
// description and the URL of every entry are the ones actually published. A
// hand-written index of a docs tree is wrong within a fortnight.
//
// Written to public/, which Astro copies to the root of the built site, so it
// lands beside the pages it describes at whatever `base` this site uses.
const LLMS_TITLE = 'Azure Emulator Family';
const LLMS_BLURB = 'The certified set: a bill of materials pinning every emulator, a docker compose that stands them up together, and a chain test that proves the combination rather than the parts.';

function writeLlms(entries) {
  const origin = 'https://calvinchengx.github.io';
  const out = [`# ${LLMS_TITLE}`, '', `> ${LLMS_BLURB}`, '', '## Documentation', ''];
  for (const e of entries) {
    const url = `${origin}${BASE}${e.slug}/`;
    out.push(e.desc ? `- [${e.title}](${url}): ${e.desc}` : `- [${e.title}](${url})`);
  }
  out.push('');
  const dir = join(here, '..', 'public');
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'llms.txt'), out.join('\n'));
  return entries.length;
}

rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });
const names = readdirSync(DOCS_SRC).filter((n) => DOC_RE.test(n)).sort();
for (const name of names) {
  writeFileSync(join(OUT, name), convert(name));
}
const llms = writeLlms(entries);
console.log(`sync-docs: wrote ${names.length} docs + index to src/content/docs/`);

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
export const BASE = '/azure-emulators/';

// The parity map is the one doc without a reading-order number: it is a living
// reference rather than a chapter, and its URL is just /parity/.
const PARITY_RE = /(^|\/)parity\.md$/;
// Docs are `NN-name.md` chapters, plus the un-numbered parity map.
const DOC_RE = /^(\d{2}-.*|parity)\.md$/;

// Rewrite `](./|docs/ NN-slug.md#anchor)` → `](/azure-emulators/NN-slug/#anchor)`.
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
function convertBody(raw, where = 'docs') {
  const lines = raw.split('\n');
  const h1Index = lines.findIndex((l) => /^#\s+/.test(l));
  if (h1Index >= 0) {
    lines.splice(h1Index, lines[h1Index + 1]?.trim() === '' ? 2 : 1);
  }
  return rewriteLinks(lines.join('\n').replace(/^\n+/, ''), where);
}


function convert(name) {
  const raw = readFileSync(join(DOCS_SRC, name), 'utf8');
  const h1 = raw.split('\n').find((l) => /^#\s+/.test(l));
  const title = h1 ? cleanTitle(h1.replace(/^#\s+/, '')) : name.replace(/\.md$/, '');
  let body = convertBody(raw, name);
  // Point "Edit this page" at the real source in /docs (the generated copy
  // under src/content/docs/ is git-ignored), not Starlight's default path.
  const editUrl = `${REPO_URL}/edit/main/docs/${name}`;
  const frontmatter = `---\ntitle: ${yamlEscape(title)}\neditUrl: ${yamlEscape(editUrl)}\n---\n\n`;
  return frontmatter + body;
}

function writeIndex() {
  const body = rewriteLinks(
    `The Azure emulator family, composed. **This repo runs no emulator of its own** — ` +
      `no binary, no image, no Go module. It is the neutral place where six ` +
      `independently released emulators are wired together, documented as a family, ` +
      `and *tested against each other*.

` +
      "```sh\n" +
      `docker compose up            # entra + arm + keyvault
` +
      `docker compose --profile fabric up   # …adds fabric
` +
      `docker compose --profile apim up         # …adds apim
` +
      `docker compose --profile databricks up   # …adds databricks
` +
      "```\n" +
      `
ARM governs the vault, as it does in Azure: role assignments decide who may ` +
      `read a secret, and **no assignment means no access**.

` +
      `:::note
No single emulator's CI can verify the family. entra's tests prove entra issues ` +
      `correct tokens; ARM's prove ARM validates *some* issuer. Neither proves that ARM ` +
      `validates *entra's* tokens, nor that the six images boot together in the right ` +
      `order. That cross-cutting proof has to live somewhere neutral — here.
:::

` +
      `## Start here

` +
      `- [Quickstart](01-quickstart.md) — the whole family in one command
` +
      `- [The family](02-the-family.md) — who the members are, and why the boundary between them is real
` +
      `- [Release coordination](03-release-coordination.md) — the BOM, the three consumption channels, and the ordering rule
` +
      `- [The chain test](04-chain-test.md) — the one check no single repo can make

` +
      `## The members

` +
      `| Emulator | Docs |
` +
      `|---|---|
` +
      `| entra-emulator | [site](https://calvinchengx.github.io/entra-emulator/) |
` +
      `| arm-emulator | [site](https://calvinchengx.github.io/arm-emulator/) |
` +
      `| azure-keyvault-emulator | [site](https://calvinchengx.github.io/azure-keyvault-emulator/) |
` +
      `| fabric-emulator | [site](https://calvinchengx.github.io/fabric-emulator/) |
` +
      `| azure-apim-emulator | [site](https://calvinchengx.github.io/azure-apim-emulator/) |
` +
      `| databricks-emulator | [site](https://calvinchengx.github.io/databricks-emulator/) |
`,
  );
  const frontmatter =
    `---
title: Azure Emulators
description: The Azure emulator family, composed — entra, ARM, Key Vault, Fabric and API Management, pinned to a certified set and tested against each other.
editUrl: false
---

`;
  writeFileSync(join(OUT, 'index.md'), frontmatter + body);
}

rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });
const names = readdirSync(DOCS_SRC).filter((n) => DOC_RE.test(n)).sort();
for (const name of names) {
  writeFileSync(join(OUT, name), convert(name));
}
writeIndex();
console.log(`sync-docs: wrote ${names.length} docs + index to src/content/docs/`);

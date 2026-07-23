#!/usr/bin/env node
// Single source of truth for release notes is the CHANGELOG array in src/main.js
// (it drives the in-app "What's new"). This renders that same data to Markdown so
// GitHub release pages and CHANGELOG.md never drift from what the app shows.
//
//   node scripts/changelog.mjs                 → rewrite CHANGELOG.md (whole history)
//   node scripts/changelog.mjs --version 1.2.0 → print one version's notes to stdout
//
// No network, no secrets: it only reads main.js and writes Markdown.

import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const scriptDir = fileURLToPath(new URL('./', import.meta.url));
const mainJsPath = fileURLToPath(new URL('../src/main.js', import.meta.url));
const changelogPath = fileURLToPath(new URL('../../CHANGELOG.md', import.meta.url));

// Pull the array literal straight out of main.js and evaluate just that expression.
// The source is our own trusted file, so this stays a single authoring location
// rather than a hand-copied second list that can silently fall out of sync.
async function loadChangelog() {
  const source = await readFile(mainJsPath, 'utf8');
  const start = source.indexOf('const CHANGELOG = [');
  if (start === -1) throw new Error('CHANGELOG array not found in main.js');
  const open = source.indexOf('[', start);
  let depth = 0;
  let end = -1;
  for (let i = open; i < source.length; i++) {
    if (source[i] === '[') depth++;
    else if (source[i] === ']' && --depth === 0) { end = i + 1; break; }
  }
  if (end === -1) throw new Error('CHANGELOG array is not closed');
  const literal = source.slice(open, end);
  // Function() over eval so the snippet can't touch this scope; input is a static
  // array of our own strings, never user data.
  return Function(`return (${literal});`)();
}

function renderVersion(entry) {
  const lines = [];
  for (const section of entry.notes) {
    lines.push(`### ${section.h}`);
    for (const item of section.items) lines.push(`- ${item}`);
    lines.push('');
  }
  return lines.join('\n').trimEnd();
}

function renderAll(changelog) {
  const blocks = [
    '# Changelog',
    '',
    'All notable changes to XynMacro. This file is generated from the in-app',
    '"What\'s new" notes by `scripts/changelog.mjs` — edit those, not this file.',
    '',
  ];
  for (const entry of changelog) {
    blocks.push(`## ${entry.version}`, '', renderVersion(entry), '');
  }
  return blocks.join('\n').replace(/\n{3,}/g, '\n\n').trimEnd() + '\n';
}

const versionArg = (() => {
  const i = process.argv.indexOf('--version');
  return i !== -1 ? process.argv[i + 1] : null;
})();

const changelog = await loadChangelog();

if (versionArg) {
  const entry = changelog.find((e) => e.version === versionArg);
  if (!entry) {
    process.stderr.write(`No changelog entry for version ${versionArg}\n`);
    process.exit(1);
  }
  process.stdout.write(renderVersion(entry) + '\n');
} else {
  await writeFile(changelogPath, renderAll(changelog), 'utf8');
  process.stderr.write(`Wrote ${changelogPath} (${changelog.length} versions)\n`);
}

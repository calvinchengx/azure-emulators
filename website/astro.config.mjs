// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import { remarkMermaid } from './plugins/remark-mermaid.mjs';

// Published to GitHub Pages under /azure-emulators/, so every generated link
// needs that base. Docs content is generated from /docs by
// scripts/sync-docs.mjs before build — /docs stays the single source of truth,
// and its files keep working as plain Markdown on GitHub.
export default defineConfig({
  site: 'https://calvinchengx.github.io',
  base: '/azure-emulators/',
  markdown: { remarkPlugins: [remarkMermaid] },
  integrations: [
    starlight({
      title: 'Azure Emulators',
      description:
        'The Azure emulator family, composed — entra, ARM, Key Vault, Fabric and API Management, pinned to a certified set and tested against each other.',
      social: [
        {
          icon: 'github',
          label: 'GitHub',
          href: 'https://github.com/calvinchengx/azure-emulators',
        },
      ],
      components: { Head: './src/components/Head.astro' },
      sidebar: [
        {
          label: 'Getting started',
          items: [{ slug: 'index' }, { slug: '01-quickstart' }],
        },
        {
          label: 'The family',
          items: [{ slug: '02-the-family' }, { slug: '03-release-coordination' }],
        },
        {
          label: 'Verification',
          items: [{ slug: '04-chain-test' }],
        },
        {
          label: 'Members',
          items: [
            { label: 'entra-emulator', link: 'https://calvinchengx.github.io/entra-emulator/', attrs: { target: '_blank' } },
            { label: 'arm-emulator', link: 'https://calvinchengx.github.io/arm-emulator/', attrs: { target: '_blank' } },
            { label: 'azure-keyvault-emulator', link: 'https://calvinchengx.github.io/azure-keyvault-emulator/', attrs: { target: '_blank' } },
            { label: 'fabric-emulator', link: 'https://calvinchengx.github.io/fabric-emulator/', attrs: { target: '_blank' } },
            { label: 'azure-apim-emulator', link: 'https://calvinchengx.github.io/azure-apim-emulator/', attrs: { target: '_blank' } },
            { label: 'databricks-emulator', link: 'https://github.com/calvinchengx/databricks-emulator', attrs: { target: '_blank' } },
          ],
        },
      ],
    }),
  ],
});

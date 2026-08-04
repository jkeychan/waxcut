import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// This runs in Node.js - Don't use client-side code here (browser APIs, JSX...)

const config: Config = {
  title: 'waxcut',
  tagline:
    'Frame-accurate, lossless MP3 splitting and duration parsing in pure Python — no ffmpeg, no subprocess, no decode step.',
  favicon: 'img/favicon.ico',

  // Future flags, see https://docusaurus.io/docs/api/docusaurus-config#future
  future: {
    v4: true, // Improve compatibility with the upcoming Docusaurus v4
  },

  // Set the production url of your site here
  url: 'https://waxcut.pages.dev',
  // Set the /<baseUrl>/ pathname under which your site is served
  baseUrl: '/',

  // Used to build GitHub edit links; not a GitHub Pages deployment.
  organizationName: 'jkeychan', // Usually your GitHub org/user name.
  projectName: 'waxcut', // Usually your repo name.

  onBrokenLinks: 'throw',

  scripts: [
    {
      src: 'https://static.cloudflareinsights.com/beacon.min.js',
      type: 'module',
      'data-cf-beacon': '{"token": "911a5ab21940492ebb33fbd4fc895af5"}',
    },
  ],

  // Even if you don't use internationalization, you can use this field to set
  // useful metadata like html lang. For example, if your site is Chinese, you
  // may want to replace "en" with "zh-Hans".
  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/jkeychan/waxcut/tree/main/site/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: 'img/social-card.jpg',
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'waxcut',
      logo: {
        alt: 'waxcut Logo',
        src: 'img/logo.png',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docsSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          href: 'https://github.com/jkeychan/waxcut',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {
              label: 'Getting Started',
              to: '/docs/getting-started',
            },
            {
              label: 'API Reference',
              to: '/docs/api-reference',
            },
          ],
        },
        {
          title: 'More',
          items: [
            {
              label: 'GitHub',
              href: 'https://github.com/jkeychan/waxcut',
            },
            {
              label: 'PyPI',
              href: 'https://pypi.org/project/waxcut/',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} waxcut. Built with Docusaurus.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;

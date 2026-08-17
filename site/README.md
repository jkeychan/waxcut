# Website

This website is built using [Docusaurus](https://docusaurus.io/), a modern static website generator.

## Installation

```bash
npm install
```

**Note**: feel free to use the package manager of your choice.

## Local Development

```bash
npm run start
```

This command starts a local development server and opens up a browser window. Most changes are reflected live without having to restart the server.

## Build

```bash
npm run build
```

This command generates static content into the `build` directory and can be served using any static contents hosting service. [`site.yml`](../.github/workflows/site.yml) runs this build on every PR touching `site/**`, so a breaking change (including a broken internal link -- `onBrokenLinks: 'throw'` in `docusaurus.config.ts`) fails CI before merge.

## Deployment

This site is hosted on [Cloudflare Pages](https://pages.cloudflare.com/), connected directly to this repository via Cloudflare's own Git integration -- pushing to `main` triggers a Cloudflare-side build and deploy automatically. There is no deploy script or manual step to run from this repo; `npm run build` above is for local verification only.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lumawen/.github/main/lumawen-logo-700-Bold-white-transparent-square-512x512.png?v=2">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/lumawen/.github/main/lumawen-logo-700-Bold-black-transparent-square-512x512.png?v=2">
    <img alt="lumawen" src="https://raw.githubusercontent.com/lumawen/.github/main/lumawen-logo-700-Bold-black-transparent-square-512x512.png?v=2" width="160">
  </picture>
</p>

# lumawen.com

The Lumawen website: https://lumawen.com

Lumawen makes hexaphonic pickups, custom guitars, an AI-assisted way to learn guitar and music theory,
and open-source guitar hardware. The site is built in public by maktig and a crew of AI
collaborators.

## What's in this repo

| Folder | What | License |
| --- | --- | --- |
| `site/` | The landing page (static HTML, no build step) | MIT |
| `api/` | Python API: accounts, mailing list, team journal, review queue | AGPL-3.0 |
| `app/` | Flutter app: web dashboard and Android app (coming soon) | MIT |
| `deploy/` | Deploy scripts and server config, no secrets (coming soon) | MIT |

Hardware designs live in their own repos and use CERN-OHL-S v2:

- [hexaphonic-pickups](https://github.com/lumawen/hexaphonic-pickups)
- [lumawen-guitar](https://github.com/lumawen/lumawen-guitar)

The Lumawen name, logo and Lumawen Sans wordmark are trademarks and are not covered by these
licenses. Lumawen Sans itself is under the SIL Open Font License 1.1 (`site/assets/fonts/OFL.txt`).

## Developing

See [api/README.md](api/README.md) for running the API and its tests.

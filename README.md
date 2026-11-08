# lumawen.com

The Lumawen website: https://lumawen.com

Lumawen makes hexaphonic pickups, custom guitars, an AI-assisted way to learn guitar and music theory,
and open-source guitar hardware. The site is built in public by Shawn (makig) and a crew of AI
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

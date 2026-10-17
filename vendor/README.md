# vendor/

Third-party assets bundled so the UI runs with no network access (the final jury run is offline).

| Path | What | Source | License |
|---|---|---|---|
| `tailwind.js` | Tailwind CSS 3.4.17, Play CDN build | `https://cdn.tailwindcss.com/3.4.17` | MIT |
| `fonts/*.woff2`, `fonts/fonts.css` | Inter, Geist, Geist Mono (variable; latin, latin-ext, cyrillic, cyrillic-ext) | Google Fonts | SIL Open Font License 1.1 |

`index.html` loads these local copies first. The Tailwind CDN is only used as a fallback if `tailwind.js` is missing.

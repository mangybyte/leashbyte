# Third-party licences

This page lists the third-party code LeashByte depends on, the licences involved and where the notices live. It was last brought up to date on 2026-11-09.

## Python

[`python-licenses.json`](python-licenses.json) lists the 65 locked Linux runtime packages, counting the local Python SDK. It's built from the package metadata in the environment that `uv sync --locked` creates.

After a dependency change, regenerate and check it:

```bash
uv sync --locked
uv run python scripts/python_licenses.py
uv run python scripts/python_licenses.py --check
```

The check fails on a missing, unknown, AGPL or SSPL licence, and on any licence expression that isn't on its reviewed list. The two exceptions below are matched on both the package name and the full expression, and the script also compares the installed versions with `uv.lock`.

The MCP packages use MIT, BSD-3-Clause or Apache-2.0 licences.

Two packages fall outside our Apache-2.0-compatible policy, and we haven't decided yet whether to replace them or keep them as exceptions:

| Package | Version | Declared licence | How we use it |
| --- | --- | --- | --- |
| `certifi` | 2026.10.23 | MPL-2.0 | Unmodified CA certificate package. |
| `tld` | 0.13.2 | MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later | Unmodified transitive dependency. |

Neither package's source is copied into the repository. The licence rule in [DESIGN.md](DESIGN.md) doesn't record an exception for them yet, and the decision is tracked in [ROADMAP.md](ROADMAP.md).

Python wheels keep their licence files in the installed `dist-info` directories. Playwright also keeps its bundled driver notices, and the Anthropic package keeps its vendored `httpx-aiohttp` notice and licence.

## JavaScript

[`../sdk/typescript/THIRD_PARTY_LICENSES.json`](../sdk/typescript/THIRD_PARTY_LICENSES.json) lists the 92 locked development packages. The TypeScript SDK has no runtime npm dependencies, so these are only the tools that build it, generate the OpenAPI types, run the tests and produce this inventory.

After a dependency change, run:

```bash
npm ci --prefix sdk/typescript
npm --prefix sdk/typescript run licenses
```

It fails on any licence expression outside the reviewed allowlist. Local paths and contact fields are stripped from the report, and each entry keeps its licence identifier, its source repository where one is known, and the package-relative path of its licence file.

The inventory has MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, Python-2.0, CC0-1.0 and CC-BY-3.0 terms. The Creative Commons entries cover package data or documentation, and no npm package supplies runtime code to the SDK.

## Container images

| Image or component | Reference in this tree | Licence and notice location |
| --- | --- | --- |
| uv build tool | `ghcr.io/astral-sh/uv:0.13.4` | Apache-2.0 OR MIT. Only the `uv` and `uvx` binaries are copied into the builder stage. |
| Python runtime | `python:3.13-slim` | Python PSF-2.0 and Debian package licences. The image keeps the `/usr/share/doc` copyright files. |
| PostgreSQL | `postgres:17.6-alpine` | PostgreSQL licence and Alpine package licences. The image keeps its upstream licence files. |
| SearXNG | `searxng/searxng@sha256:c750ee1d859b26d3aeae51c07f6a48c3a0bfbaa9195d71a18b258862d88c4c3c` | AGPL-3.0. A separate, unmodified HTTP service. Source: `https://github.com/searxng/searxng`. |
| Test fixture | `python:3.13-slim` | Only in the test profile, with no production host port. |
| Google Chrome | Installed by `playwright install --with-deps chrome` | Proprietary Google Chrome terms. The browser image also keeps the Playwright and operating-system notices. |

When we last built the stack, the base-image tags resolved to these digests:

| Image | Digest |
| --- | --- |
| `ghcr.io/astral-sh/uv:0.13.4` | `sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b` |
| `python:3.13-slim` | `sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90` |
| `postgres:17.6-alpine` | `sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94` |

SearXNG is the only AGPL service, and neither licence inventory contains an AGPL or SSPL package. LeashByte doesn't import or copy any SearXNG code. Search requests go to its HTTP API over the Compose network.

Third-party notices stay in their own packages and images. The root [NOTICE](../NOTICE) holds the extra attributions that have to travel with source and SDK distributions.

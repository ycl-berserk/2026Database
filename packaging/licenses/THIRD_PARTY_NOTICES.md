# Third-party notices

Learning Space （学习空间） desktop bundles the following third-party components.
Version pins for build tooling live in `packaging/requirements.txt` and
`packaging/postgres/manifest.json`; Python runtime pins live in `uv.lock`.

## PostgreSQL 16

- License: PostgreSQL License (permissive, BSD-like)
- Source: https://www.postgresql.org/about/licence/
- Bundled as a trimmed EDB binary distribution under `resources/pgsql/`.

## pgvector 0.8.0

- License: PostgreSQL License
- Source: https://github.com/pgvector/pgvector/blob/master/LICENSE

## Python 3.13 runtime

- License: Python Software Foundation License 2.0
- Source: https://docs.python.org/3/license.html

## PyInstaller 6.22.3

- License: GPL 2.0 with a special exception for bundled applications
- Source: https://pyinstaller.org/en/stable/license.html

## OpenSSL

- License: Apache License 2.0
- Source: https://www.openssl.org/source/license.html

## ICU

- License: Unicode License V3
- Source: https://github.com/unicode-org/icu/blob/main/LICENSE

## Tauri 2

- License: Apache-2.0 OR MIT
- Source: https://github.com/tauri-apps/tauri/blob/dev/LICENSE_APACHE

## Python dependencies

The sidecar bundles the packages pinned in `uv.lock`, including FastAPI (MIT),
Starlette (BSD-3-Clause), Jinja2 (BSD-3-Clause), psycopg (LGPL-3.0), pydantic
(MIT), uvicorn (BSD-3-Clause), sqlglot (MIT) and sqlparse (BSD-3-Clause).

Full license texts are shipped inside the corresponding Python distributions
(`*.dist-info/licenses/`) collected by PyInstaller into the sidecar `_internal`
directory.

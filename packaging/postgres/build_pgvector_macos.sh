#!/usr/bin/env bash
# Build pgvector against the locally built PostgreSQL (macOS).
set -euo pipefail

VERSION="${PGVECTOR_VERSION:-0.8.0}"
SHA256="${PGVECTOR_SHA256:-867a2c328d4928a5a9d6f052cd3bc78c7d60228a9b914ad32aa3db88e9de27b0}"
URL="https://codeload.github.com/pgvector/pgvector/tar.gz/refs/tags/v${VERSION}"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-13.0}"

PREFIX=""
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)
            PREFIX="$2"
            shift 2
            ;;
        *)
            echo "unknown argument: $1" >&2
            exit 2
            ;;
    esac
done
if [ -z "$PREFIX" ]; then
    echo "--prefix is required" >&2
    exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
CACHE="$ROOT/packaging/.cache"
JOBS="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
ARCHIVE="$CACHE/pgvector-v${VERSION}.tar.gz"
SOURCE="$CACHE/pgvector-${VERSION}"
PG_CONFIG="$PREFIX/bin/pg_config"

if [ ! -x "$PG_CONFIG" ]; then
    echo "pg_config not found under $PREFIX" >&2
    exit 1
fi
mkdir -p "$CACHE"
if [ ! -f "$ARCHIVE" ]; then
    echo "[pgvector] downloading v$VERSION"
    curl -fL --retry 3 --retry-delay 2 -o "$ARCHIVE.part" "$URL"
    mv "$ARCHIVE.part" "$ARCHIVE"
fi
actual="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
if [ "$actual" != "$SHA256" ]; then
    rm -f "$ARCHIVE"
    echo "pgvector archive SHA-256 mismatch: $actual" >&2
    exit 1
fi
if [ ! -d "$SOURCE" ]; then
    tar -xzf "$ARCHIVE" -C "$CACHE"
fi

make -C "$SOURCE" PG_CONFIG="$PG_CONFIG" -j"$JOBS"
make -C "$SOURCE" PG_CONFIG="$PG_CONFIG" install
echo "[pgvector] installed into $PREFIX"

#!/usr/bin/env bash
# Build the PostgreSQL runtime from the pinned release tarball (macOS).
#
# Layout matches the bundled runtime on Windows (bin/ lib/ share/ + headers),
# so packaging/build.py can trim it after pgvector has been built against it.
set -euo pipefail

# `uv run` prepends .venv/bin to PATH, where the project installs a console
# script named `ls`; it would shadow /bin/ls and break autoconf's `ls -di .`
# probe. Keep the system tools ahead of any virtualenv entries.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

VERSION="${PG_VERSION:-16.10}"
SHA256="${PG_SHA256:-de8485f4ce9c32e3ddfeef0b7c261eed1cecb54c9bcd170e437ff454cb292b42}"
URL="https://ftp.postgresql.org/pub/source/v${VERSION}/postgresql-${VERSION}.tar.bz2"
# 与 tauri.macos.conf.json 的 minimumSystemVersion 保持一致；
# CI 在更新的 macOS 上编译，不设会把这些二进制的最低版本抬到 runner 的系统版本。
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-13.0}"

PREFIX=""
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)
            PREFIX="$2"
            shift 2
            ;;
        --force)
            FORCE=1
            shift
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
ARCHIVE="$CACHE/postgresql-${VERSION}.tar.bz2"
SOURCE="$CACHE/postgresql-${VERSION}"

mkdir -p "$CACHE"
if [ "$FORCE" = "1" ]; then
    rm -rf "$PREFIX" "$SOURCE"
fi

if [ ! -f "$ARCHIVE" ]; then
    echo "[pg] downloading PostgreSQL $VERSION"
    curl -fL --retry 3 --retry-delay 2 -o "$ARCHIVE.part" "$URL"
    mv "$ARCHIVE.part" "$ARCHIVE"
fi
actual="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
if [ "$actual" != "$SHA256" ]; then
    rm -f "$ARCHIVE"
    echo "postgresql archive SHA-256 mismatch: $actual" >&2
    exit 1
fi
if [ ! -d "$SOURCE" ]; then
    tar -xjf "$ARCHIVE" -C "$CACHE"
fi

mkdir -p "$PREFIX"
OPENSSL_PREFIX="${OPENSSL_PREFIX:-$(brew --prefix openssl@3 2>/dev/null || true)}"
if [ -z "$OPENSSL_PREFIX" ] || [ ! -f "$OPENSSL_PREFIX/include/openssl/evp.h" ]; then
    echo "[pg] OpenSSL headers not found; pgcrypto requires OpenSSL on PostgreSQL 16" >&2
    exit 1
fi
(
    cd "$SOURCE"
    ./configure \
        --prefix="$PREFIX" \
        --without-icu \
        --without-readline \
        --without-libxml \
        --with-openssl \
        --with-includes="$OPENSSL_PREFIX/include" \
        --with-libraries="$OPENSSL_PREFIX/lib" \
        --without-lz4 \
        --without-zstd \
        --disable-nls
)
make -C "$SOURCE" -j"$JOBS"
make -C "$SOURCE" install
make -C "$SOURCE/contrib/pg_trgm" install
make -C "$SOURCE/contrib/pg_stat_statements" install
make -C "$SOURCE/contrib/pgcrypto" install

# Bundle OpenSSL dylibs and rewrite absolute Homebrew paths so the app is self-contained.
for lib in "$OPENSSL_PREFIX"/lib/libssl*.dylib "$OPENSSL_PREFIX"/lib/libcrypto*.dylib; do
    [ -f "$lib" ] || continue
    cp -L "$lib" "$PREFIX/lib/"
done
for binary in "$PREFIX"/bin/* "$PREFIX"/lib/*.dylib "$PREFIX"/lib/postgresql/*.dylib; do
    [ -f "$binary" ] || continue
    otool -L "$binary" 2>/dev/null | awk '/libssl|libcrypto/ {print $1}' | while read -r dep; do
        install_name_tool -change "$dep" "@rpath/$(basename "$dep")" "$binary" 2>/dev/null || true
    done
done
for lib in "$PREFIX"/lib/libssl*.dylib "$PREFIX"/lib/libcrypto*.dylib; do
    [ -f "$lib" ] || continue
    install_name_tool -id "@rpath/$(basename "$lib")" "$lib" 2>/dev/null || true
done
echo "[pg] installed into $PREFIX (OpenSSL: $OPENSSL_PREFIX)"

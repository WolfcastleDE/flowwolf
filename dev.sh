#!/bin/sh
# Runs cargo inside the official Rust image (no local toolchain/linker needed).
# Usage: ./dev.sh test | ./dev.sh build --release
cd "$(dirname "$0")"
MSYS_NO_PATHCONV=1 exec docker run --rm -v "$(pwd -W 2>/dev/null || pwd):/src" -v flowwolf-target:/target \
  -v flowwolf-cargo:/usr/local/cargo/registry -e CARGO_TARGET_DIR=/target -w /src rust:1-bookworm cargo "$@"

#!/usr/bin/env bash
# Reproduce the ARM64 static BusyBox binary used in v6; source/tools are not
# stored in this repository. Requires git, make, Python3, pip and Zig 0.13.0.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${1:-/tmp/recovery-v6-busybox}"
mkdir -p "$WORK"
python3 -m pip install --quiet --target "$WORK/zigpy" 'ziglang==0.13.0'
ZIG="$WORK/zigpy/ziglang/zig"
"$ZIG" version
if [[ ! -d "$WORK/busybox/.git" ]]; then
  git clone --depth 1 --branch 1_36_1 https://github.com/mirror/busybox.git "$WORK/busybox"
fi
[[ "$(git -C "$WORK/busybox" rev-parse HEAD)" == 1a64f6a20aaf6ea4dbba68bbfa8cc1ab7e5c57c4 ]]
cat > "$WORK/zigcc" <<EOF
#!/usr/bin/env python3
import os, sys
args = []
for a in sys.argv[1:]:
    if a.startswith('-Wp,-MD,'):
        args += ['-MD', '-MF', a[len('-Wp,-MD,'):]]
    elif a in ('-Wl,--warn-common', '-Wl,--sort-common',
               '-Wl,--sort-section,alignment', '-Wl,--verbose') or a.startswith('-Wl,-Map,'):
        continue
    else:
        args.append(a)
os.execv('$ZIG', ['zig', 'cc', '-target', 'aarch64-linux-musl', '-static', *args])
EOF
cat > "$WORK/zigar" <<EOF
#!/bin/sh
exec "$ZIG" ar "\$@"
EOF
chmod +x "$WORK/zigcc" "$WORK/zigar"
(
  cd "$WORK/busybox"
  make -s clean
  cp "$ROOT/v6/busybox.config" .config
  # Pin out the build timestamp so the resulting binary is reproducible.
  # Avoid parallel Zig cache writes when using the same global cache.
  KCONFIG_NOTIMESTAMP=1 ZIG_GLOBAL_CACHE_DIR="$WORK/zig-cache" make -j1 \
    CC="$WORK/zigcc" AR="$WORK/zigar" LD="$WORK/zigcc" \
    HOSTCC=gcc SKIP_STRIP=y busybox
)
python3 "$ROOT/tools/build_shell_recovery.py" "$WORK/busybox/busybox"

# v6 experimental recovery shell

`recovery-v6-shell-package.zip` contains `recovery-v6-shell.img` and
`FLASH-AND-TEST.txt`. Extract the image before flashing; read the safety and
rollback steps first. **Not tested on the actual phone.** v5 remains the last
known-to-boot image reported by the user. The goal is to make `adb shell id`
and `adb shell ls /` work on Infinix HOT 8 X650C / H626 recovery.

The v5 ramdisk has `adbd` but no `/system/bin/sh`. v6 adds static AArch64
BusyBox 1.36.1 (ash, id, ls, cat, etc.) at
`/system_root/system/bin/sh`; the existing `/system` symlink resolves there.
Applet symlinks (such as `id -> sh`) live in the same directory. `init.rc`
adds a `restorecon` on that file and `plat_file_contexts` labels its real path
`shell_exec` for SELinux. It keeps the rest of the v5 recovery and its
embedded key. The shell may run as the unprivileged `shell` user, not root;
protected files can still say "Permission denied". This package does **not**
implement the on-screen approval popup planned separately.

## Build provenance and verification

- BusyBox source: `mirror/busybox` tag `1_36_1`, commit
  `1a64f6a20aaf6ea4dbba68bbfa8cc1ab7e5c57c4`.
- Toolchain: `ziglang==0.13.0` PyPI package; target `aarch64-linux-musl`,
  static; BusyBox options pinned in `busybox.config`.
- Rebuild command: `bash tools/build_busybox_v6.sh` from the repo root.
  It fetches the source/toolchain into `/tmp`, builds there, then repacks v5
  using `tools/build_shell_recovery.py`. Nothing is flashed by this command.
- BusyBox SHA-256: `f64e6d3f4bf28ed05ad478be570194d03cc65fef9d62e934a005ba16a095bee8`.
  Static ELF64 AArch64: no interpreter or dynamic section. Two independent
  rebuilds produced identical BusyBox and image SHA-256 hashes.
- Image SHA-256: `cf08a395011e00ec3bf330144acf067d80c6547719407f225a98992874afab12`.
- ZIP SHA-256: `9dc0f3cc21bde0915270baf22e6ca1c8c196041e0ec5ee051f201ab2c13bea91`.
- Image remains 33,554,432 bytes (the v5 partition size). The entire kernel
  and every unchanged ramdisk entry are identical to v5. Shell entries were
  added; only `init.rc` and `plat_file_contexts` were edited. Old DTBO (42,066
  bytes) and vbmeta (1,344 bytes) were preserved byte-for-byte but **moved**:
  new DTBO offset 16,736,256; new vbmeta offset 16,779,264. Header ramdisk
  size, boot ID and DTBO pointer, plus AVB footer offsets, were updated.
  Repacked gzip and cpio were parsed again; ZIP CRC was checked.

**Critical limitation:** The vbmeta **signature and hash descriptor were NOT
regenerated**. Relocating DTBO/vbmeta and enlarging the ramdisk may also be
incompatible with this bootloader. A device with enforced AVB will reject it;
flash only if you have already disabled verification and have a verified way
to restore v5. ELF compatibility, init SELinux relabeling and ADB shell
execution still require on-device testing. Do not mistake offline structure
checks for a successful device test.

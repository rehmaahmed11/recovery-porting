# recovery-porting

Infinix HOT 8 (X650C / H626, MT6765) recovery images patched for development use.

## Packages

- **`v5/recovery-v5-package.zip` — current release.** Cumulative:
  MTK adb auto-start trigger + your PC's key in `/adb_keys` +
  `ro.adb.secure=1`. Your PC connects directly in recovery; unknown PCs are
  rejected (`unauthorized`). This is the config the on-screen authorize
  daemon (see `POPUP-AUTH-PLAN.md`, planned v6) plugs into.
- `recovery-adb-auto-package[1].zip` — base image. Ramdisk patch: MediaTek
  configfs ADB trigger in `init.recovery.mt6765.rc` changed from
  `on property:ro.debuggable=0` to `=1` so adbd starts in recovery.
- `recovery-adb-authorized-package.zip` — same image, plus the PC's ADB public
  key (`adbkey[1].pub`, `AHMED@DESKTOP-6J676F2`) embedded as `/adb_keys` in the
  ramdisk. adbd reads that file at startup, so this PC is pre-authorized in
  recovery mode with no RSA confirmation prompt.

Kernel payload, recovery_dtbo (42066 bytes @ offset 16601088), AVB/vbmeta tail,
total size (33554432 bytes), and every other ramdisk file are byte-identical to
the source image. Only the ramdisk gained one `adb_keys` entry; header
`ramdisk_size` and `id[]` were updated accordingly.

**Safety:** images are NOT re-signed — flash only on an unlocked bootloader with
AVB verification off, and keep a known-good recovery.img restoration path.
Details in each package's `*-README.txt`.

# recovery-porting

Infinix HOT 8 (X650C / H626, MT6765) recovery images patched for development use.

## Packages

- **`v6/recovery-v6-shell-package.zip` — experimental, untested on-device.**
  Adds a static ARM64 BusyBox shell and basic commands to v5. See
  `v6/README.md` and `v6/FLASH-AND-TEST.txt` for risks, flashing steps and
  rollback. v5 remains the last image reported to boot successfully.
- **`v5/recovery-v5-package.zip` — last known-to-boot package.** Cumulative:
  MTK adb auto-start trigger + your PC's key in `/adb_keys` +
  `ro.adb.secure=1`. Your PC connects directly in recovery; unknown PCs are
  rejected (`unauthorized`). This is the config the on-screen authorize
  daemon (see `POPUP-AUTH-PLAN.md`, not yet implemented) plugs into.
- `recovery-adb-auto-package[1].zip` — base image. Ramdisk patch: MediaTek
  configfs ADB trigger in `init.recovery.mt6765.rc` changed from
  `on property:ro.debuggable=0` to `=1` so adbd starts in recovery.
- `recovery-adb-authorized-package.zip` — same image, plus the PC's ADB public
  key (`adbkey[1].pub`, `AHMED@DESKTOP-6J676F2`) embedded as `/adb_keys` in the
  ramdisk. adbd reads that file at startup, so this PC is pre-authorized in
  recovery mode with no RSA confirmation prompt.

For **v5**, the kernel payload, recovery_dtbo (42066 bytes @ offset 16601088),
AVB/vbmeta tail, total size (33554432 bytes), and every other ramdisk file
are byte-identical to its source image. Only its ramdisk gained one `adb_keys`
entry; header `ramdisk_size` and `id[]` were updated accordingly. **v6 moves
recovery_dtbo and vbmeta** to make space for the shell; see `v6/README.md`.

## Checking recovery ADB

After booting into recovery, run `adb devices`. An authorized, connected phone
normally appears as `SERIAL    recovery` (not `device`, which is the usual state
for a booted Android system). This means ADB sees the recovery; it is **not**
an error. `unauthorized` means the PC's ADB key was not accepted; no on-screen
authorization prompt is included in v5 (see `POPUP-AUTH-PLAN.md`).

**ADB shell limitation in v5 (v6 attempts to fix this):** the recovery ramdisk
includes `adbd` but has no `/system/bin/sh`. The `system` entry is a symlink to
`/system_root/system`; the shell is not available through that path in this
user's recovery session. Therefore `adb shell id` fails with
`exec '/system/bin/sh' failed: No such file or directory`. This is not an ADB
authorization failure. `adb pull /init.rc recovery-init.txt` also fails on the
tested device with `Permission denied`: the ramdisk's `/init.rc` is owned by
root and mode 0750, and is not readable through this ADB session. Do not use
that command as a connection test. `adb devices` showing `recovery` is enough
to confirm the ADB connection; it does **not** prove shell or file-transfer
access. A usable `adb shell` requires a separate, tested recovery image that
provides a compatible shell at `/system/bin/sh` (and any commands you intend
to run).

`adb reboot bootloader` switches to fastboot; check there with `fastboot
devices`. `fastboot reboot recovery` switches back to recovery, where you
should use `adb devices` again. These commands do not flash an image.

**Safety:** images are NOT re-signed — flash only on an unlocked bootloader with
AVB verification off, and keep a known-good recovery.img restoration path.
Read the instructions inside the ZIP before flashing.

# On-screen ADB authorization in recovery — technical plan

Goal: on the Infinix HOT 8 (X650C, MT6765) in recovery mode —
PC with a known RSA key connects instantly; an **unknown** PC triggers an
on-screen "Allow USB debugging?" dialog on the phone, with allow/deny
(and optional "always allow").

## Executive summary

**No rebuilt adbd is needed.** The stock adbd already contains the full
"ask the framework" machinery; it just has no one to ask in recovery. We
add a tiny static binary, `recovery_authd`, that connects to adbd's auth
socket and plays the role that `system_server` plays in normal Android —
but instead of a Java dialog it draws a confirmation screen on the
framebuffer with minui and reads the volume/power keys.

Stack: **stock adbd (untouched) + `ro.adb.secure=1` + `/adb_keys` (your PC) +
`recovery_authd` (new, ~150–300 KB static AArch64 binary) + 2 init.rc lines.**

---

## 1. Verified facts (from this repo's image, not assumptions)

| Fact | Evidence |
|---|---|
| Boot image `os_version` decodes to **Android 9.0.0, 2019-08 patch** | header field `0x12000138` |
| `sbin/adbd` is **statically linked AArch64**, no `PT_INTERP`/`PT_DYNAMIC` | ELF header parse |
| Its auth code matches **AOSP android-9.0.0 (`system/core/adb/adbd_auth.cpp`)** byte-for-byte string-wise | `Failed to write PK`, `Failed to get adbd socket`, `PK%s` all present in binary; symbols `adbd_auth_listener`, `adbd_auth_event`, `adbd_auth_confirm_key`, `adbd_cloexec_auth_socket` present |
| adbd's auth-talk socket is created by init: `socket adbd stream 660 system system` | ramdisk `init.rc` service block |
| Ramdisk currently has `ro.adb.secure=0` → **no auth at all, no PK events** | `prop.default` + gating below |

## 2. The exact framework ↔ adbd protocol (android-9.0.0_r61)

From `system/core/adb/adbd_auth.cpp`:

- `adbd_auth_init()` — fd from `android_get_control_socket("adbd")` (init's
  service socket, i.e. **`/dev/socket/adbd`**, socket namespace used by
  `LocalSocketAddress.Namespace.RESERVED` on the Java side), `listen(fd, 4)`.
  Note: adbd should also be connected via **abstract** name `adbd` as
  fallback — try both, use whichever succeeds.
- Unknown host key → `adbd_auth_confirm_key(key, len, t)`:
  - `framework_fd < 0` → `"Client not connected"`, sets `needs_retry`, host
    stays pending. When a client connects later (`adbd_auth_listener`),
    `send_auth_request()` is re-issued, the PC re-sent its key and the `PK`
    message flows then. **Our daemon can start at boot and never miss a request.**
  - else → `unix_write(framework_fd, "PK" + key)` — one write; `key` is the
    full public-key line sent by the PC (`base64blob user@host`, NUL-terminated).
- Response — `adbd_auth_event()` reads **exactly 2 bytes**:
  - `"OK"` → `adbd_auth_verified()` → PC is authorized and connected.
  - anything else (`"NO"` included, or EOF) → connection is **not**
    authorized; on EOF adbd marks the framework client as disconnected.

So the wire protocol is: connect → read `"PK…"` → write `"OK"` (allow) or
write nothing/"NO" (deny). Version caveat: this is the 9.0.0 behavior; if we
ever swap adbd for a newer one (10+: `framework_authentication` results enum),
re-check.

## 3. The hard prerequisite: `ro.adb.secure=1`

`adb/daemon/main.cpp` (9.0.0):

```cpp
if (ALLOW_ADBD_NO_AUTH && !GetBoolProperty("ro.adb.secure", false)) {
    auth_required = false;
}
```

With the current ramdisk (`ro.adb.secure=0`): `auth_required=false`, adbd
calls `handle_online()`+`send_connect()` for **every** PC — the `PK` path
never executes, so *no popup is ever possible*. Flipping
`ro.adb.secure=0 → =1` in `prop.default` activates:
**known key (`/adb_keys`) → instant connect; unknown key → `PK` to our daemon.**

## 4. `recovery_authd` design

Single static AArch64 ELF (same style as stock adbd; the ramdisk has no
shared libc for it to need). Approx. 150–300 KB.

```
                    ┌──────────────┐  PK<base64 user@host>  ┌─────────────────┐
  PC (unknown key)  │  stock adbd  │ ─────────────────────▶ │ recovery_authd  │
  ────────────────▶ │  (unmodified)│ ◀───── "OK" or "NO" ──  │                 │
                    └──────────────┘                         │  /dev/socket/adbd│
                                                             │  base64+md5     │ ── fingerprint XX:XX:…
                                                             │  minui (fbdev)  │ ── "Allow USB debugging?" screen
                                                             │  evdev keys     │ ── Vol+/Vol− choose, Power select
                                                             │  persist writer │ ── see §5
                                                             └─────────────────┘
```

Pieces and their provenance:

| Component | Source | Notes |
|---|---|---|
| minui (fbdev backend only) | `bootable/recovery` android-9.0 (`minui/graphics_fbdev.cpp`, `graphics.cpp`, `resources.cpp`, `font_10x18.h`, …) | Vendor + strip: compile *only* `graphics_fbdev` (drop adf/drm/overlay → kills the libdrm dependency). MTK 4.9 kernels expose a classic fbdev on `/dev/graphics/fb0`; the stock recovery UI renders on it today. BGRA32/line-length quirks on MTK are handled by TWRP with `RECOVERY_BGRA` / `RECOVERY_GRAPHICS_USE_LINELENGTH`-style tweaks — worst case we add the same few lines. |
| Input (evdev) | minui `events.cpp` or ~40 raw lines on `/dev/input/event*` | Keys: `KEY_VOLUMEUP`/`KEY_VOLUMEDOWN` move, `KEY_POWER` select. Same nodes stock recovery reads. |
| base64 decode | ~60 lines, public domain | Decode the key blob (524 bytes expected). |
| MD5 fingerprint | ~150 lines, public domain | Format like the Android dialog: MD5 of decoded key, hex, colon-separated. Matches what `adb` prints on the PC (`adb key` fingerprint), so the user can visually compare. |
| Socket client | libc only | connect `/dev/socket/adbd` (fallback abstract `"adbd"`); retry loop like `UsbDebuggingThread` (reconnect after 1s if adbd restarts). |
| Auth-loop | see §6 |

## 5. Persistence ("always allow this computer")

- **Runtime (this boot):** append the approved key line to `/adb_keys` —
  the ramdisk rootfs is writable RAM, and 9.0 adbd re-reads both key files on
  *every* verify attempt (`adbd_auth_verify` loops `"/adb_keys"`,
  `"/data/misc/adb/adb_keys"` per call), so approval sticks immediately.
  (The `"OK"` response already authorizes the pending connection; the file
  append covers reconnects/replug.)
- **Across reboots:** write to `/data/misc/adb/adb_keys` (create dir,
  mode 0640) — best case, and it also authorizes the PC in normal Android boot.
  **Caveat:** /data is FBE-encrypted when a lockscreen is set; recovery sees
  encrypted userdata then. Fallback chain: try mount /data → else write
  `/persist/adb_authorized_keys` (MTK `/persist` is small but present) → else
  session-only. On boot, a one-shot init service copies `/persist/…` into
  `/adb_keys` before adbd starts. (v1 can ship session-only + /data attempt;
  persist-fallback is polish.)

## 6. Auth-loop pseudocode

```c
for (;;) {
    fd = connect_auth_socket();          // /dev/socket/adbd, else \0adbd, retry 1s
    for (;;) {
        n = read_line(fd, buf);          // blocks until adbd writes "PK<key>"
        if (n <= 0) break;               // adbd died/restarted -> reconnect
        if (strncmp(buf, "PK", 2)) continue;
        key = buf + 2;                   // "base64blob user@host"
        fp  = md5_fingerprint(key);
        choice = ui_dialog("Allow USB debugging?", fp, key_comment,
                           {"Allow", "Always allow", "Deny"});   // minui + keys
        if (choice != DENY) {
            write(fd, "OK", 2);
            append_line("/adb_keys", key);                 // runtime
            if (choice == ALWAYS) persist_key(key);        // §5
        }
        // deny: send nothing -> PC stays 'unauthorized'
        ui_close();                                        // leave framebuffer
    }
    close(fd);
}
```

## 7. Ramdisk integration (we already have the repack pipeline)

1. `prop.default`: `ro.adb.secure=0` → `1`, and keep `ro.secure=0`
   (document the deliberate trade: adbd runs as root in recovery — that stays).
2. Add `/sbin/recovery_authd` (static, 0755).
3. `init.rc` / `init.recovery.mt6765.rc`:
   ```
   service recovery_authd /sbin/recovery_authd
       class main
       user root
       seclabel u:r:recovery:s0
   ```
   plus the boot service that syncs persisted keys → `/adb_keys`.
4. Repack + rezip with the existing verified pipeline.

**SELinux:** recovery carries a cut-down policy; `recauthd` needs
`/dev/socket/adbd` (connect), `/dev/graphics/fb0` + `/dev/input/event*`
(rw), optional `/data` mount. If the stock policy denies, we patch the
ramdisk `sepolicy` blob (it's a regular file in the ramdisk) or run the
service in a domain that already has the rights — flagged for on-device
testing; `adb shell dmesg` will show any avc denials immediately.

## 8. Known limitations / honest caveats

- **Framebuffer contention:** while the stock `recovery` menu is up, our
  dialog draws *over* it (last-writer-wins; stock UI only repaints on key
  events). After the dialog closes, press any key to make the recovery menu
  repaint. Acceptable for v1; a v2 could integrate the dialog into a
  rebuilt recovery UI for a polished hand-off.
- **9.0 deny semantics:** adbd only acts on `"OK"`; a denied PC stays
  `unauthorized` until it disconnects (normal-Android warns similarly).
- **Touch:** v1 is hardware-keys only (stock recovery on this device is also
  key-driven). Touch can be added via evdev later.
- Requires the same flashing constraints as before (unlocked bootloader /
  AVB off, unsigned image).

## 9. Build routes

**Route A — standalone static build (recommended for v1):**
vendor `minui/` (fbdev only) + ~600 lines of daemon C; cross-compile with an
AArch64 NDK/LLVM toolchain, `-static`. No AOSP tree needed. Fast iteration:
push `recovery_authd` over `adb push` to a booted recovery and test before
baking the image.

**Route B — build inside a TWRP/Omni minimal tree:** proper Soong separation
and free GUI bits, but requires assembling a device tree for X650C; only
worth it if you later want a full custom recovery.

## 10. Milestones

1. [x] Reverse-engineer protocol & confirm stock adbd compatibility (this doc)
2. [ ] `recovery_authd` skeleton: socket + PK parse + md5, log-only, LTO — test on device in recovery via `adb push`
3. [ ] minui dialog + key input
4. [~] persistence chain + init.rc + `ro.adb.secure=1` — **shipped in v5** (`v5/recovery-v5-package.zip`): secure=1 + `/adb_keys`; persistence chain still pending the daemon
5. [ ] repack image, on-device verification matrix:
   your PC (direct), unknown PC (dialog → deny → `unauthorized`), unknown PC
   (dialog → allow → connects), always-allow survives reboot.

## Reference sources

- `system/core/adb/adbd_auth.cpp` @ android-9.0.0_r61 — protocol above
- `adb/daemon/main.cpp` @ android-9.0.0_r61 — `ro.adb.secure` gating
- `frameworks/base/.../UsbDebuggingManager.java` @ android-9.0.0_r61 — reference client (`PK` parse, `"OK"`/`"NO"`, dialog+fingerprint)
- `bootable/recovery/minui/` (android-9.0 branch) — fbdev graphics + evdev input

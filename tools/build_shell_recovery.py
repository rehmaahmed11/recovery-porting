#!/usr/bin/env python3
"""Repack v5 as a shell-enabled recovery (offline; no flashing).

Usage: python3 tools/build_shell_recovery.py /path/to/aarch64-static-busybox
Requires only Python stdlib. The input binary must be trusted and independently
compiled for AArch64 Linux; see v6/README.md for its exact source/config.
"""
import gzip
import hashlib
from pathlib import Path
import struct
import sys
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'v5/recovery-v5-package.zip'
OUT = ROOT / 'v6/recovery-v6-shell-package.zip'
IMAGE_NAME = 'recovery-v6-shell.img'
V5_IMAGE_SHA256 = '4f5128dfe9b568dab7fabecfcc930e8bda5bc0e8f4ad0e3c6e796050ae4fc189'
PAGE_MIN = 2048


def align(n, page):
    return (n + page - 1) // page * page


def sha(b):
    return hashlib.sha256(b).hexdigest()


def entries(cpio):
    """Iterate newc entries including trailer; validate boundaries and names."""
    off = 0
    while off < len(cpio):
        assert cpio[off:off + 6] == b'070701', ('invalid newc', off)
        fields = [int(cpio[off + 6 + j * 8:off + 14 + j * 8], 16) for j in range(13)]
        mode, size, nlen = fields[1], fields[6], fields[11]
        assert 1 < nlen < 4096
        name_end = off + 110 + nlen
        name = cpio[off + 110:name_end]
        assert name[-1:] == b'\0'
        data_off = align(name_end, 4)
        end = align(data_off + size, 4)
        assert end <= len(cpio)
        yield name[:-1].decode('ascii'), mode, cpio[data_off:data_off + size], off, end, fields
        off = end
        if name == b'TRAILER!!!\0':
            assert off == len(cpio), 'unexpected cpio trailing bytes'
            break


def record(name, mode, data, ino):
    """One deterministic newc entry, owned by root and world-readable."""
    name = name.encode('ascii') + b'\0'
    fields = (ino, mode, 0, 0, 2 if mode & 0o170000 == 0o040000 else 1,
              0, len(data), 0, 0, 0, 0, len(name), 0)
    hdr = b'070701' + b''.join(f'{i:08x}'.encode() for i in fields)
    header = hdr + name
    header += b'\0' * (align(len(header), 4) - len(header))
    return header + data + b'\0' * (align(len(data), 4) - len(data))


def updated_record(name, fields, data):
    """Re-encode an existing newc entry with changed content but same metadata."""
    name_bytes = name.encode('ascii') + b'\0'
    fields = list(fields)
    fields[6] = len(data)
    assert fields[11] == len(name_bytes)
    header = b'070701' + b''.join(f'{i:08x}'.encode() for i in fields) + name_bytes
    return (header + b'\0' * (align(len(header), 4) - len(header)) +
            data + b'\0' * (align(len(data), 4) - len(data)))


def shell_entries(busybox, start_ino):
    prefix = 'system_root/system'
    additions = [(prefix, 0o040755, b''),
                 (prefix + '/bin', 0o040755, b''),
                 (prefix + '/bin/sh', 0o100755, busybox)]
    # Use relative links so PATH commands also work; no second ELF copy.
    applets = ('cat', 'chmod', 'chown', 'cp', 'dmesg', 'env', 'find', 'grep',
               'head', 'id', 'ls', 'mkdir', 'mount', 'mv', 'ps', 'rm', 'sleep',
               'tail', 'umount', 'uname')
    additions.extend((prefix + '/bin/' + a, 0o120777, b'sh') for a in applets)
    return b''.join(record(*row, start_ino + i) for i, row in enumerate(additions)), additions


def build(busybox):
    assert busybox[:4] == b'\x7fELF' and busybox[4] == 2 and busybox[5] == 1
    assert struct.unpack_from('<H', busybox, 18)[0] == 183, 'requires AArch64'
    assert b'/lib/ld-' not in busybox, 'binary appears dynamically linked'
    with ZipFile(BASE) as z:
        base = z.read('recovery-v5.img')
    assert sha(base) == V5_IMAGE_SHA256, 'unexpected base image; refusing to repack'
    assert base[:8] == b'ANDROID!'
    kernel_size, ram_size, second_size = (struct.unpack_from('<I', base, n)[0]
                                          for n in (8, 16, 24))
    page = struct.unpack_from('<I', base, 36)[0]
    assert page >= PAGE_MIN and page & (page - 1) == 0
    assert struct.unpack_from('<I', base, 40)[0] == 1  # boot image header v1
    assert second_size == 0
    ram_off = align(page + kernel_size, page)
    dtbo_size = struct.unpack_from('<I', base, 1632)[0]
    old_dtbo_off = struct.unpack_from('<Q', base, 1636)[0]
    old_footer_off = len(base) - 64
    magic, maj, minor, old_orig_size, old_vb_off, vb_size = struct.unpack_from(
        '>4sIIQQQ', base, old_footer_off)
    assert magic == b'AVBf' and (maj, minor) == (1, 0)
    assert old_dtbo_off == 16601088 and dtbo_size == 42066
    assert old_vb_off == 16646144 and vb_size == 1344
    assert old_orig_size == 16644096
    assert base[old_vb_off:old_vb_off + 4] == b'AVB0'
    kernel = base[page:page + kernel_size]
    dtbo = base[old_dtbo_off:old_dtbo_off + dtbo_size]
    vbmeta = base[old_vb_off:old_vb_off + vb_size]
    compressed = base[ram_off:ram_off + ram_size]
    old_cpio = gzip.decompress(compressed)
    originals = list(entries(old_cpio))
    assert originals[-1][0] == 'TRAILER!!!'
    assert not any(name.startswith('system_root/system') for name, *_ in originals)
    assert dict((n, (mode, data)) for n, mode, data, *_ in originals)['system'] == (
        0o120777, b'/system_root/system')
    assert any(n == 'adb_keys' for n, *_ in originals)
    initrc = dict((n, data) for n, _, data, *_ in originals)['init.rc']
    assert initrc.count(b'    mkdir /system\n') == 1
    patched_initrc = initrc.replace(
        b'    mkdir /system\n',
        b'    mkdir /system\n    restorecon /system_root/system/bin/sh\n')
    contexts = dict((n, data) for n, _, data, *_ in originals)['plat_file_contexts']
    assert b'/system/bin/sh\t\t--\tu:object_r:shell_exec:s0' in contexts
    assert b'/system_root/system/bin/sh' not in contexts
    patched_contexts = (contexts +
        b'/system_root/system/bin/sh\t\t--\tu:object_r:shell_exec:s0\n')
    patches = {'init.rc': patched_initrc, 'plat_file_contexts': patched_contexts}
    extra, expected = shell_entries(busybox, max(row[5][0] for row in originals) + 1)
    chunks = []
    for n, mode, data, begin, end, fields in originals:
        if n == 'TRAILER!!!':
            chunks.append(extra)
        chunks.append(updated_record(n, fields, patches[n]) if n in patches
                      else old_cpio[begin:end])
    new_cpio = b''.join(chunks)
    new_entries = list(entries(new_cpio))
    assert len(new_entries) == len(originals) + len(expected)
    assert [(n, m, d) for n, m, d, *_ in new_entries[:len(originals)-1]] == [
        (n, m, patches.get(n, d)) for n, m, d, *_ in originals[:-1]]
    assert [(n, m, d) for n, m, d, *_ in new_entries[len(originals)-1:-1]] == expected
    new_ram = gzip.compress(new_cpio, compresslevel=9, mtime=0)
    # The new ramdisk exceeds the old DTBO/vbmeta location. Move both blobs
    # into the existing zero padding; update their pointers, never truncate.
    dtbo_off = align(ram_off + len(new_ram), page)
    vb_off = align(dtbo_off + dtbo_size, page)
    assert dtbo_off > old_vb_off
    assert vb_off + align(vb_size, page) < old_footer_off
    assert not any(base[align(old_vb_off + vb_size, page):old_footer_off]), \
        'unexpected data in image padding'
    out = bytearray(base)
    # Erase old ramdisk, DTBO and vbmeta after saving the originals.
    out[ram_off:align(old_vb_off + vb_size, page)] = b'\0' * (
        align(old_vb_off + vb_size, page) - ram_off)
    out[ram_off:ram_off + len(new_ram)] = new_ram
    out[dtbo_off:dtbo_off + dtbo_size] = dtbo
    out[vb_off:vb_off + vb_size] = vbmeta
    struct.pack_into('<I', out, 16, len(new_ram))
    struct.pack_into('<Q', out, 1636, dtbo_off)
    boot_id = hashlib.sha1(kernel + struct.pack('<I', kernel_size) + new_ram +
                           struct.pack('<I', len(new_ram)) + struct.pack('<I', second_size)).digest()
    out[576:608] = boot_id + b'\0' * 12
    struct.pack_into('>QQ', out, old_footer_off + 12, vb_off, vb_off)
    # Original_image_size is size of the boot image before appended vbmeta.
    assert struct.unpack_from('>QQQ', out, old_footer_off + 12) == (
        vb_off, vb_off, vb_size)
    assert len(out) == len(base) == 33554432
    assert out[page:page + kernel_size] == kernel
    assert out[dtbo_off:dtbo_off + dtbo_size] == dtbo
    assert out[vb_off:vb_off + vb_size] == vbmeta
    assert gzip.decompress(out[ram_off:ram_off + len(new_ram)]) == new_cpio
    assert out[-64:-36] != base[-64:-36]  # footer positions changed
    assert out[-28:] == base[-28:]
    assert out[:8] == base[:8]
    return bytes(out), dtbo_off, vb_off, len(new_ram)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    binary = Path(sys.argv[1]).read_bytes()
    img, dtbo_off, vb_off, ram_size = build(binary)
    OUT.parent.mkdir(exist_ok=True)
    readme = (OUT.parent / 'FLASH-AND-TEST.txt').read_bytes()
    with ZipFile(OUT, 'w', compression=ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(IMAGE_NAME, img)
        z.writestr('FLASH-AND-TEST.txt', readme)
    with ZipFile(OUT) as z:
        assert z.testzip() is None
        assert sha(z.read(IMAGE_NAME)) == sha(img)
    print(f'Image SHA256: {sha(img)}')
    print(f'BusyBox SHA256: {sha(binary)}')
    print(f'Ramdisk {ram_size} bytes; DTBO offset {dtbo_off}; vbmeta offset {vb_off}')
    print(f'Package: {OUT}')


if __name__ == '__main__':
    main()

"""Unpack a PyInstaller sidecar so the privacy gate can read what it ships.

The frozen sidecar stores its files and Python modules zlib-compressed, so a scan
of the raw .exe sees almost nothing. This writes every CArchive entry, and every
module in each embedded PYZ as decompressed marshal bytes, under OUT_DIR.

    unpack_sidecar.py SIDECAR_EXE OUT_DIR
"""

import os
import sys

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def unpack(sidecar_exe, out_dir):
    archive = CArchiveReader(sidecar_exe)
    written = 0
    for name in archive.toc:
        data = archive.extract(name)
        if data is None:
            continue
        entry_path = os.path.join(out_dir, "carchive", *name.split("/"))
        _write(entry_path, data)
        written += 1
        if name.endswith(".pyz") or name.startswith("PYZ"):
            modules = ZlibArchiveReader(entry_path)
            for module in modules.toc:
                code = modules.extract(module, raw=True)
                if code is None:  # namespace package, no data
                    continue
                module_path = os.path.join(out_dir, "pyz", *module.split(".")) + ".pyc.raw"
                _write(module_path, code)
                written += 1
    return written


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip().splitlines()[-1].strip())
    count = unpack(sys.argv[1], sys.argv[2])
    if count == 0:
        sys.exit("No entries unpacked; this does not look like a PyInstaller sidecar.")
    print(f"unpacked {count} entries")

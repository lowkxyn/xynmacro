"""Write the branded setup exe without the NSIS installer it carries.

XynMacro-Setup embeds the NSIS installer byte for byte. That installer is scanned
unpacked in its own step; its compressed bytes would only add noise here (random
zlib data occasionally spells a credential pattern), so this cuts them out and
fails if the setup doesn't carry exactly the installer that was scanned.

    strip_setup_payload.py SETUP_EXE INSTALLER_EXE OUT_FILE
"""

import sys


def strip(setup_exe, installer_exe, out_file):
    with open(setup_exe, "rb") as handle:
        setup = handle.read()
    with open(installer_exe, "rb") as handle:
        installer = handle.read()
    start = setup.find(installer)
    if start == -1:
        raise SystemExit("The setup does not contain the scanned installer.")
    with open(out_file, "wb") as handle:
        handle.write(setup[:start] + setup[start + len(installer):])
    print(f"Setup without its {len(installer)}-byte installer -> {out_file}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    strip(*sys.argv[1:])

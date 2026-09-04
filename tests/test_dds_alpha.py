"""DXT3 texture decode verification.

The terrain blend relies on the layer2 texture alpha channel being a real
splat mask (straight alpha, not premultiplied). This decodes the DDS files
independently (pure Python) and checks the mask characteristics.

Reference values for JDT01 (Junon/JD); update if testing other data.

Exit code 0 on success, 1 on failure.
"""
import os
import struct
import sys

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _paths

TEXTURE_DIR = os.environ.get(
    "ROSE_TEST_TEXTURES",
    _paths.client_terrain_tiles_dir(),
)


def decode_dxt3(path):
    """Decode a DXT3 DDS to (rgb, alpha) per pixel."""
    with open(path, "rb") as f:
        data = f.read()
    assert data[:4] == b"DDS ", f"{path}: not a DDS file"
    w, h = struct.unpack_from("<II", data, 12)
    # Pixel-format FourCC at header offset 80 (file offset 84): the block
    # layout below is only valid for DXT3. Decoding DXT1/DXT5 bytes as DXT3
    # yields plausible-looking means, so check explicitly.
    fourcc = data[84:88]
    assert fourcc == b"DXT3", f"{path}: expected DXT3, got FourCC {fourcc!r}"
    body = data[128:]
    bx, by = w // 4, h // 4
    out = []
    for b_y in range(by):
        for b_x in range(bx):
            off = (b_y * bx + b_x) * 16
            alpha_b = body[off:off + 8]
            c0, c1 = struct.unpack_from("<HH", body, off + 8)
            ind = struct.unpack_from("<I", body, off + 12)[0]

            def exp(c):
                return ((c >> 11) & 0x1F) * 255 // 31, \
                       ((c >> 5) & 0x3F) * 255 // 63, \
                       (c & 0x1F) * 255 // 31

            p = [exp(c0), exp(c1)]
            if c0 > c1:
                p += [tuple((p[0][i] * 2 + p[1][i]) // 3 for i in range(3)),
                      tuple((p[0][i] + p[1][i] * 2) // 3 for i in range(3))]
            else:
                p += [tuple((p[0][i] + p[1][i]) // 2 for i in range(3)), (0, 0, 0)]
            for ty in range(4):
                for tx in range(4):
                    nib = ty * 4 + tx
                    a = ((alpha_b[nib // 2] >> (4 * (nib % 2))) & 0xF) / 15.0
                    out.append((p[(ind >> (2 * nib)) & 3], a))
    return w, h, out


# (file, expected alpha mean range) - from the 2026-08-01 analysis
CHECKED = [
    ("T011_01.dds", (0.3, 0.5)),
    ("T018_01.dds", (0.3, 0.5)),
    ("T021_05.dds", (0.5, 0.7)),
    ("T020_01.dds", (0.2, 0.4)),
]


def main():
    if not os.path.isdir(TEXTURE_DIR):
        print(f"texture dir not found: {TEXTURE_DIR}")
        print("set ROSE_TEST_TEXTURES to the terrain tiles directory")
        return 1

    fail = 0
    for name, (lo, hi) in CHECKED:
        path = os.path.join(TEXTURE_DIR, name)
        if not os.path.isfile(path):
            print(f"{name}: MISSING")
            fail += 1
            continue
        w, h, px = decode_dxt3(path)
        n = len(px)
        alphas = [a for _, a in px]
        mean = sum(alphas) / n
        black_rgb = sum(1 for rgb, a in px if sum(rgb) / 3 < 20)
        # straight-alpha sanity: transparent pixels must not be premultiplied
        # black (premultiplied data would be black where alpha is low)
        low_alpha_black = sum(1 for rgb, a in px if a < 0.1 and sum(rgb) / 3 < 20)
        # Spatial layout check: the alpha mass must not concentrate in one
        # quadrant. A global mean is invariant to nibble/row permutation, so
        # a scrambled decode would still pass the mean check above.
        quad_mass = [0.0, 0.0, 0.0, 0.0]
        for y in range(h):
            for x in range(w):
                a = alphas[y * w + x]
                quad_mass[(y * 2 // h) * 2 + (x * 2 // w)] += a
        total_mass = sum(quad_mass) or 1.0
        max_share = max(quad_mass) / total_mass
        var = sum((a - mean) ** 2 for a in alphas) / n
        layout_ok = max_share < 0.9 and var > 1e-6
        status = "OK" if (lo <= mean <= hi) and black_rgb / n < 0.01 and layout_ok else "FAIL"
        if status != "OK":
            fail += 1
        print(f"{name}: {w}x{h} alpha mean={mean:.3f} "
              f"black_rgb={black_rgb} ({100 * black_rgb / n:.2f}%) "
              f"black@alpha<0.1={low_alpha_black} "
              f"maxquad={max_share:.2f} [{status}]")

    if fail:
        print(f"\n{len(CHECKED) - fail}/{len(CHECKED)} passed, {fail} FAILURES")
        return 1
    print("\nDDS ALPHA OK (straight splat masks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

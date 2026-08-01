"""DDS writer for textures exported from Blender.

The client's custom DDS loader (src/dds_image_loader.rs) accepts
uncompressed 32-bit pixels in addition to DXT formats, so new textures
are written as standard BGRA 32-bit DDS (R mask 0x00ff0000 etc.), which
the loader reports as B8G8R8A8 and converts to R8G8B8A8.

Layout is the classic DDS_HEADER (no DX10 extension), single mip level.
"""
import struct

DDSD_CAPS = 0x1
DDSD_HEIGHT = 0x2
DDSD_WIDTH = 0x4
DDSD_PITCH = 0x8
DDSD_PIXELFORMAT = 0x1000
DDSD_MIPMAPCOUNT = 0x20000
DDSD_LINEARSIZE = 0x80000

DDPF_ALPHAPIXELS = 0x1
DDPF_RGB = 0x40

DDSCAPS_TEXTURE = 0x1000
DDSCAPS_COMPLEX = 0x8

PIXEL_FORMAT_SIZE = 32  # sizeof(DDS_PIXELFORMAT)


def write_dds_rgba8(filepath, width, height, rgba_bytes):
    """Write an uncompressed 32-bit RGBA DDS file.

    Args:
        filepath: destination path
        width, height: image dimensions
        rgba_bytes: packed RGBA8 pixels, width*height*4 bytes, rows top-down
    """
    if len(rgba_bytes) != width * height * 4:
        raise ValueError(
            f"pixel buffer {len(rgba_bytes)} bytes does not match "
            f"{width}x{height} RGBA8")

    # Convert RGBA -> BGRA byte order (standard DDS layout for these masks)
    bgra = bytearray(len(rgba_bytes))
    for i in range(0, len(rgba_bytes), 4):
        bgra[i] = rgba_bytes[i + 2]
        bgra[i + 1] = rgba_bytes[i + 1]
        bgra[i + 2] = rgba_bytes[i]
        bgra[i + 3] = rgba_bytes[i + 3]

    flags = (DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH |
             DDSD_PIXELFORMAT)
    header = struct.pack(
        "<4s7I", b"DDS ", 124,
        flags,
        height, width, width * 4, 0, 0, 0)

    pf_flags = DDPF_RGB | DDPF_ALPHAPIXELS
    # DDS_PIXELFORMAT: size, flags, fourcc, rgb bit count, 4 channel masks
    pixel_format = struct.pack("<I", PIXEL_FORMAT_SIZE)
    pixel_format += struct.pack("<I", pf_flags)
    pixel_format += struct.pack("<I", 0)
    pixel_format += struct.pack("<I", 32)
    pixel_format += struct.pack("<IIII", 0x00FF0000, 0x0000FF00,
                                0x000000FF, 0xFF000000)

    caps = DDSCAPS_TEXTURE
    caps_header = struct.pack("<I", caps)
    caps_header += struct.pack("<IIIII", 0, 0, 0, 0, 0)

    with open(filepath, "wb") as f:
        f.write(header)
        f.write(pixel_format)
        f.write(caps_header)
        f.write(bgra)

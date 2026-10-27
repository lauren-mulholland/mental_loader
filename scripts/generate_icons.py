"""Rasterise a simple code-native monogram without an image service or extra dependency."""

import struct
import zlib
from pathlib import Path


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def generate(size):
    pixels = bytearray()
    for row in range(size):
        pixels.append(0)
        y = row / size
        for column in range(size):
            x = column / size
            # Two arches and stems, plus a full stop: m.
            stem = any(
                left <= x <= right and 0.39 <= y <= 0.72
                for left, right in ((0.18, 0.25), (0.38, 0.45), (0.58, 0.65))
            )
            arch = any(
                0.07 <= ((x - cx) ** 2 + (y - 0.42) ** 2) ** 0.5 <= 0.14 and y <= 0.43
                for cx in (0.315, 0.515)
            )
            dot = (x - 0.77) ** 2 + (y - 0.68) ** 2 <= 0.04**2
            pixels.extend((247, 248, 243) if stem or arch or dot else (36, 92, 80))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
    png += chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")
    path = Path(__file__).parents[1] / "src/mental_loader/static" / f"icon-{size}.png"
    path.write_bytes(png)


if __name__ == "__main__":
    for size in (192, 512):
        generate(size)

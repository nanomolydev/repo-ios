#!/usr/bin/env python3
"""Locate the port's green "Ok" button in a screenshot.

  python3 tools/find_button.py screen.png   ->  "1170 1580"

Blind taps at guessed coordinates hit the subscribe link instead, which opens
a browser. The button is the only saturated-green text in the bottom half, so
find it by colour rather than by guessing a layout.
"""
import sys

from PIL import Image


def find_green(path: str, bottom_fraction: float = 0.5):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    y0 = int(h * (1 - bottom_fraction))
    px = im.load()
    xs, ys = [], []
    for y in range(y0, h, 2):
        for x in range(0, w, 2):
            r, g, b = px[x, y]
            if g > 140 and g - r > 50 and g - b > 40:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return sum(xs) // len(xs), sum(ys) // len(ys)


def main(argv=None):
    argv = argv or sys.argv[1:]
    hit = find_green(argv[0])
    if not hit:
        return 1
    print(hit[0], hit[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())

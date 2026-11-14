"""Draw a simple app icon and write an .icns file."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "AppIcon.icns")
    try:
        from AppKit import (  # type: ignore
            NSBezierPath,
            NSColor,
            NSFont,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSImage,
            NSMakeRect,
            NSMutableParagraphStyle,
            NSParagraphStyleAttributeName,
            NSString,
            NSTextAlignmentCenter,
        )
    except Exception:
        print("AppKit not available; skipping icon", file=sys.stderr)
        return 1

    size = 1024
    img = NSImage.alloc().initWithSize_((size, size))
    img.lockFocus()
    # Teal rounded square
    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.10, 0.45, 0.48, 1.0).set()
    path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(64, 64, 896, 896), 180, 180
    )
    path.fill()
    # Document card
    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.97, 0.96, 0.92, 1.0).set()
    card = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(262, 230, 500, 620), 36, 36
    )
    card.fill()
    # Text lines
    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.10, 0.45, 0.48, 0.85).set()
    for y, w in ((680, 360), (600, 300), (520, 340), (440, 260)):
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(320, y, w, 28), 8, 8
        ).fill()
    # Character
    para = NSMutableParagraphStyle.alloc().init()
    para.setAlignment_(NSTextAlignmentCenter)
    attrs = {
        NSFontAttributeName: NSFont.systemFontOfSize_weight_(220, 0.5),
        NSForegroundColorAttributeName: NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.10, 0.35, 0.38, 1.0
        ),
        NSParagraphStyleAttributeName: para,
    }
    NSString.stringWithString_("字").drawInRect_withAttributes_(
        NSMakeRect(262, 250, 500, 280), attrs
    )
    img.unlockFocus()

    png = Path(tempfile.mkdtemp()) / "icon.png"
    tiff = img.TIFFRepresentation()
    tiff.writeToFile_atomically_(str(png.with_suffix(".tiff")), True)
    subprocess.run(
        ["/usr/bin/sips", "-s", "format", "png", str(png.with_suffix(".tiff")), "--out", str(png)],
        check=True,
        capture_output=True,
    )
    iconset = Path(tempfile.mkdtemp()) / "AppIcon.iconset"
    iconset.mkdir()
    for s, name in (
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ):
        subprocess.run(
            ["/usr/bin/sips", "-z", str(s), str(s), str(png), "--out", str(iconset / name)],
            check=True,
            capture_output=True,
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["/usr/bin/iconutil", "-c", "icns", "-o", str(dest), str(iconset)], check=True)
    print(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

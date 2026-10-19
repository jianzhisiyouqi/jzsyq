"""Static radar screen for Echo's ST7796 LCD; no sensor or servo operations.

The command-scale rays are a visualization convention, not calibrated physical
angles. No target or scanline is drawn until real data is deliberately supplied.
"""

import math
from PIL import Image, ImageDraw, ImageFont


def _font(size):
    for candidate in ("DejaVuSans.ttf",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                      "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
                      "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def rgb565_bytes(image):
    """Convert a PIL RGB image to the driver's high-byte-first RGB565 format."""
    import numpy as np

    rgb = np.asarray(image.convert("RGB"), dtype=np.uint16)
    packed = ((rgb[:, :, 0] >> 3) << 11
              | (rgb[:, :, 1] >> 2) << 5
              | (rgb[:, :, 2] >> 3))
    return packed.astype(">u2").tobytes()


def radar_background(max_cm=120, ring_step_cm=30):
    """Return a 480x320 half-circle grid without synthetic measurements."""
    if not isinstance(max_cm, int) or not isinstance(ring_step_cm, int):
        raise ValueError("Range and ring spacing must be positive integers.")
    if max_cm <= 0 or ring_step_cm <= 0 or ring_step_cm > max_cm:
        raise ValueError("Use 0 < ring_step_cm <= max_cm.")

    image = Image.new("RGB", (480, 320), (2, 10, 7))
    draw = ImageDraw.Draw(image)
    title, label, small = _font(22), _font(13), _font(11)
    bright = (60, 255, 140)
    grid = (16, 110, 66)
    muted = (113, 170, 144)
    center_x, center_y, radius = 240, 283, 210

    draw.rectangle((0, 0, 479, 319), outline=(26, 93, 61))
    draw.text((12, 7), "ECHO RADAR", font=title, fill=bright)
    draw.text((337, 15), f"RANGE {max_cm} cm", font=label, fill=muted)
    draw.line((12, 41, 467, 41), fill=grid)
    draw.text((13, 46), "DISPLAY TEST", font=small, fill=muted)

    # Rays show command values, not a physical-angle calibration or a measurement.
    for command in range(0, 181, 30):
        angle = math.radians(command)
        edge_x = center_x + round(radius * math.cos(angle))
        edge_y = center_y - round(radius * math.sin(angle))
        draw.line((center_x, center_y, edge_x, edge_y), fill=grid)
        text_x = center_x + round((radius + 12) * math.cos(angle)) - 9
        text_y = center_y - round((radius + 12) * math.sin(angle)) - 6
        draw.text((text_x, text_y), str(command), font=small, fill=muted)

    rings = list(range(ring_step_cm, max_cm + 1, ring_step_cm))
    if rings[-1] != max_cm:
        rings.append(max_cm)
    for distance_cm in rings:
        r = round(radius * distance_cm / max_cm)
        draw.arc((center_x-r, center_y-r, center_x+r, center_y+r),
                 180, 360, fill=grid)
        offset = round(r / math.sqrt(2))
        draw.text((center_x + offset + 6, center_y - offset + 3),
                  f"{distance_cm} cm", font=small, fill=muted)

    draw.line((center_x-radius, center_y, center_x+radius, center_y),
              fill=bright, width=2)
    draw.ellipse((center_x-3, center_y-3, center_x+3, center_y+3), fill=bright)
    draw.text((12, 300), "NO MEASUREMENT YET", font=small, fill=muted)
    draw.text((329, 300), "480 x 320 | RGB565", font=small, fill=muted)
    return image


def show_radar_background(lcd, max_cm=120, ring_step_cm=30):
    """Render and send a full frame through an already initialized ST7796LCD."""
    image = radar_background(max_cm, ring_step_cm)
    if (lcd.width, lcd.height) != image.size:
        raise ValueError("This layout requires landscape 480x320.")
    lcd.show_rgb565(rgb565_bytes(image))
    return image


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Render a local preview; no FPGA access.")
    parser.add_argument("output", help="Output PNG path")
    args = parser.parse_args()
    preview = radar_background()
    if len(rgb565_bytes(preview)) != 480 * 320 * 2:
        raise RuntimeError("Unexpected RGB565 buffer size.")
    sample = Image.new("RGB", (3, 1))
    sample.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255)])
    if rgb565_bytes(sample) != bytes.fromhex("f80007e0001f"):
        raise RuntimeError("RGB565 channel or byte order mismatch.")
    preview.save(args.output)
    print(f"Preview saved: {args.output}; RGB565 size and color-byte checks passed.")

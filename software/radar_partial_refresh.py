"""Fixed HC-SR04 display with dirty rectangles; no overlay or servo writes.

The initial frame must exactly match the image currently on the LCD. The
caller's PIL image is kept in sync after every successful rectangle transfer.
"""

import time
from PIL import ImageChops, ImageDraw
from radar_display import radar_background, rgb565_bytes, _font


class FixedSensorDisplay:
    def __init__(self, lcd, current_frame):
        if current_frame.mode != "RGB" or current_frame.size != (480, 320):
            raise ValueError("current_frame must be the displayed 480x320 RGB image.")
        self.lcd = lcd
        self.current = current_frame
        self.base = radar_background(max_cm=120, ring_step_cm=30)
        self.large_font = _font(22)
        self.small_font = _font(11)
        self.point_font = _font(12)
        self.valid = True

    def render(self, distance_cm):
        frame = self.base.copy()
        draw = ImageDraw.Draw(frame)
        result = "TIMEOUT" if distance_cm is None else f"{distance_cm:.1f} cm"
        draw.rectangle((200, 2, 477, 39), fill=(2, 10, 7))
        draw.text((220, 8), result, font=self.large_font, fill=(60, 255, 140))
        draw.rectangle((10, 45, 195, 64), fill=(2, 10, 7))
        draw.text((13, 46), "FIXED SENSOR", font=self.small_font,
                  fill=(113, 170, 144))

        if distance_cm is None:
            footer = "NO ECHO - NO TARGET"
        elif 0 < distance_cm <= 120:
            # Fixed forward sensor only: not measured servo angle feedback.
            x, y = 240, 283 - round(distance_cm / 120 * 210)
            draw.ellipse((x-8, y-8, x+8, y+8),
                         outline=(40, 220, 110), width=2)
            draw.ellipse((x-4, y-4, x+4, y+4), fill=(180, 255, 200))
            draw.text((x+12, y-8), f"{distance_cm:.1f} cm",
                      font=self.point_font, fill=(180, 255, 200))
            footer = "SINGLE MEASUREMENT"
        else:
            footer = "OUT OF DISPLAY RANGE"

        draw.rectangle((10, 297, 468, 317), fill=(2, 10, 7))
        draw.text((12, 300), footer, font=self.small_font,
                  fill=(113, 170, 144))
        return frame

    def update(self, distance_cm):
        if not self.valid:
            raise RuntimeError("A transfer failed; reinitialize LCD and restore a full frame first.")
        started = time.perf_counter()
        target = self.render(distance_cm)
        difference = ImageChops.difference(self.current, target)
        rectangles = []
        # Tight changed bounds in each 8-pixel band. A single global bounding
        # box would send unchanged pixels between header, target, and footer.
        for top in range(0, 320, 8):
            box = difference.crop((0, top, 480, top+8)).getbbox()
            if box is not None:
                left, y0, right, y1 = box
                rectangle = (left, top+y0, right, top+y1)
                if (rectangles and rectangles[-1][0] == left
                        and rectangles[-1][2] == right
                        and rectangles[-1][3] == rectangle[1]):
                    previous = rectangles[-1]
                    rectangles[-1] = (left, previous[1], right, rectangle[3])
                else:
                    rectangles.append(rectangle)

        sent_bytes = 0
        try:
            for box in rectangles:
                left, top, right, bottom = box
                patch = target.crop(box)
                data = rgb565_bytes(patch)
                self.lcd.show_rgb565(data, x=left, y=top,
                                     width=right-left, height=bottom-top)
                self.current.paste(patch, (left, top))
                sent_bytes += len(data)
        except BaseException:
            # The underlying driver resets the LCD on an interrupted transfer.
            # Do not assume the software shadow still matches the panel.
            self.valid = False
            raise
        return {"seconds": time.perf_counter()-started,
                "pixel_bytes": sent_bytes, "rectangles": len(rectangles)}

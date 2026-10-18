import os
from PIL import Image, ImageDraw, ImageOps

class SSRenderer:
    def __init__(self, rs=3):
        self.rs = rs

    def draw_bubble_shadow(self, draw, box, radius, color=(0, 0, 0, 60)):
        """Draws a smooth shadow using multiple layers for AA feel."""
        x1, y1, x2, y2 = box
        for i in range(1, 5):
            offset = i * self.rs
            alpha = int(color[3] / (i * 1.5))
            draw.rounded_rectangle(
                [x1 + offset, y1 + offset, x2 + offset, y2 + offset],
                radius=radius, fill=(0, 0, 0, alpha)
            )

    def render_avatar(self, canvas, path, pos, size):
        if not path or not os.path.exists(path):
            return
        
        try:
            av = Image.open(path).convert("RGBA")
            av = av.resize((size, size), Image.Resampling.LANCZOS)
            
            # High-quality circular mask
            mask = Image.new("L", (size, size), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, size, size), fill=255)
            
            canvas.paste(av, (int(pos[0]), int(pos[1])), mask)
        except Exception:
            pass

    def render_bubble(self, draw, box, color, radius):
        self.draw_bubble_shadow(draw, box, radius)
        draw.rounded_rectangle(box, radius=radius, fill=color)

    def finalize(self, img, scale_factor, output_path, is_png=False):
        """Final downscaling with Lanczos for razor-sharp text."""
        final_w = max(1, int(img.width / self.rs * scale_factor))
        final_h = max(1, int(img.height / self.rs * scale_factor))
        
        img = img.resize((final_w, final_h), Image.Resampling.LANCZOS)
        
        if is_png:
            img.save(output_path, "PNG", optimize=True)
        else:
            img.save(output_path, "WEBP", quality=100, lossless=True)
        
        return output_path

import uuid
import logging
from src.security.log_redaction import summarize_exception
from PIL import Image, ImageDraw
from .font_manager import FontManager
from .layout import LayoutEngine
from .renderer import SSRenderer
from .themes import THEMES, get_theme

logger = logging.getLogger(__name__)

# Telegram sticker limits
MAX_STICKER_DIMENSION = 512

class QuoteManager:
    THEMES = THEMES
    def __init__(self):
        self.rs = 4  # 4x supersampling
        self.fonts = FontManager()
        self.layout = LayoutEngine(self.rs)
        self.renderer = SSRenderer(self.rs)
        
        self.bubble_color_default = (23, 23, 35, 255)
        self.name_color = (130, 200, 255, 255)
        self.text_color = (255, 255, 255, 255)

    def _hex_to_rgba(self, hex_color, alpha=255):
        if not hex_color or not hex_color.startswith('#'):
            return self.bubble_color_default
        hex_color = hex_color.lstrip('#')
        try:
            if len(hex_color) == 6:
                r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
                return (r, g, b, alpha)
        except: pass
        return self.bubble_color_default

    def _smart_scale(self, img, user_scale, is_sticker):
        """
        Smart auto-scaling:
        - Short quotes â†’ bigger (fills the sticker canvas)
        - Long quotes â†’ smaller (fits proportionally)
        - Always respects Telegram's 512px max dimension for stickers
        """
        w, h = img.width, img.height
        
        if is_sticker:
            # Target: 512px on the longest side
            target = MAX_STICKER_DIMENSION
            ratio = min(target / w, target / h)
            
            # Apply user scale on top
            ratio *= user_scale
            
            new_w = max(1, int(w * ratio))
            new_h = max(1, int(h * ratio))
            
            # Enforce max dimension
            if max(new_w, new_h) > MAX_STICKER_DIMENSION:
                final_ratio = MAX_STICKER_DIMENSION / max(new_w, new_h)
                new_w = max(1, int(new_w * final_ratio))
                new_h = max(1, int(new_h * final_ratio))
        else:
            # PNG/file mode: just apply user scale from supersampled size
            new_w = max(1, int(w / self.rs * user_scale))
            new_h = max(1, int(h / self.rs * user_scale))
        
        return img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    def generate_quote(self, messages, output_path=None, **kwargs):
        """Main entry point."""
        if not output_path:
            ext = "png" if kwargs.get('is_png') else "webp"
            output_path = f"downloads/q_{uuid.uuid4().hex}.{ext}"

        try:
            # 1. Fonts
            name_font = self.fonts.get_font(30 * self.rs, bold=True)
            text_font = self.fonts.get_font(28 * self.rs, bold=False)
            reply_font = self.fonts.get_font(20 * self.rs, bold=False)
            # Emoji height target for inline compositing
            emoji_target_h = 28 * self.rs

            # 2. Layout
            canvas_w = 550 * self.rs
            processed_msgs, total_h, max_bw = self.layout.calculate_metrics(
                messages, name_font, text_font, reply_font, canvas_w
            )

            # 3. Canvas
            final_cw = int(self.layout.padding * 2 + self.layout.avatar_size + 20 * self.rs + max_bw)
            img = Image.new("RGBA", (final_cw, int(total_h)), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            # 4. Render
            curr_y = self.layout.padding
            bx = self.layout.padding + self.layout.avatar_size + 20 * self.rs
            theme = get_theme(kwargs.get("theme"))
            bubble_color = self._hex_to_rgba(kwargs.get('bg_color')) if kwargs.get('bg_color') else self._hex_to_rgba(theme.bubble)
            name_color = self._hex_to_rgba(kwargs.get("name_color"), 255) if kwargs.get("name_color") else theme.name_color
            text_color = self._hex_to_rgba(kwargs.get("text_color"), 255) if kwargs.get("text_color") else theme.text_color

            for m in processed_msgs:
                self.renderer.render_avatar(
                    img, m['avatar'], 
                    (self.layout.padding, curr_y), 
                    self.layout.avatar_size
                )
                
                box = [bx, curr_y, bx + m['w'], curr_y + m['h']]
                self.renderer.render_bubble(draw, box, bubble_color, 40 * self.rs)
                
                ty = curr_y + self.layout.bubble_padding
                
                # Reply
                if m['reply_text']:
                    rb = [bx + 15*self.rs, ty, bx + m['w'] - 15*self.rs, ty + 70*self.rs]
                    draw.rounded_rectangle(rb, radius=12*self.rs, fill=(255, 255, 255, 18))
                    # Accent bar (rounded ends)
                    bar_x = rb[0] + 4*self.rs
                    draw.rounded_rectangle(
                        [bar_x, rb[1] + 8*self.rs, bar_x + 5*self.rs, rb[3] - 8*self.rs],
                        radius=3*self.rs, fill=self.name_color
                    )
                    # Reply author name
                    text_x = bar_x + 16*self.rs
                    self.fonts.render_text_with_fallback(draw, (text_x, rb[1] + 8*self.rs), m['reply_name'] or '', reply_font, name_color, 20 * self.rs)
                    # Reply text preview
                    self.fonts.render_text_with_fallback(draw, (text_x, rb[1] + 36*self.rs), (m['reply_text'] or '')[:60], reply_font, (200, 200, 200), 20 * self.rs)
                    ty += 90 * self.rs

                # Name (with fallback for decorative Unicode)
                self.fonts.render_text_with_fallback(draw, (bx + self.layout.bubble_padding, ty), m['name'], name_font, name_color, 30 * self.rs)
                ty += 52 * self.rs
                
                # Text lines with inline emoji compositing
                for line in m['lines']:
                    self.fonts.render_text_with_emoji(
                        draw, (bx + self.layout.bubble_padding, ty),
                        line, text_font, text_color, emoji_target_h
                    )
                    ty += 32 * self.rs + self.layout.line_spacing
                
                curr_y += m['h'] + self.layout.msg_spacing

            # 5. Crop
            bbox = img.getbbox()
            if bbox:
                margin = 15 * self.rs
                img = img.crop((
                    max(0, bbox[0] - margin), max(0, bbox[1] - margin),
                    min(img.width, bbox[2] + margin), min(img.height, bbox[3] + margin)
                ))

            # 6. Smart Scale & Save
            user_scale = kwargs.get('scale', 1.0)
            is_png = kwargs.get('is_png', False)
            is_sticker = not is_png and not kwargs.get('as_file', False)
            
            img = self._smart_scale(img, user_scale, is_sticker)
            
            if is_png:
                img.save(output_path, "PNG", optimize=True)
            else:
                img.save(output_path, "WEBP", quality=100, lossless=True)
            
            return output_path

        except Exception as e:
            logger.error("Modular Quote System Failed: %s", summarize_exception(e, label="quote_render"))
            raise

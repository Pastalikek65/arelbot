import os
import textwrap
import uuid
import re
import logging
from PIL import Image, ImageDraw, ImageFont, ImageOps
from src.security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)

class QuoteGenerator:
    def _find_font(self, candidates):
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    def __init__(self):
        # Optimized Font candidates
        self.font_candidates = [
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "C:\\Windows\\Fonts\\arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        ]
        self.font_regular_candidates = [
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "C:\\Windows\\Fonts\\arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ]
        self.emoji_font_candidates = [
            "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
            "C:\\Windows\\Fonts\\seguiemj.ttf"
        ]
        
        self.font_path = self._find_font(self.font_candidates)
        self.font_regular_path = self._find_font(self.font_regular_candidates)
        self.emoji_font_path = self._find_font(self.emoji_font_candidates)
            
        self.canvas_width = 1024 
        self.render_scale = 3   
        self.bg_color_default = (0, 0, 0, 0)
        self.bubble_color_default = (23, 23, 35, 255)
        self.text_color = (255, 255, 255, 255)
        self.name_color = (130, 200, 255, 255)

    def _get_font(self, size, bold=True):
        """Safe font loader."""
        path = self.font_path if bold else self.font_regular_path
        size = max(1, int(size))
        if path and os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception as e:
                logger.warning("Font loading failed size=%s: %s", size, summarize_exception(e, label="font"))
        return ImageFont.load_default()

    def _get_emoji_font(self, size):
        """Ultra-safe emoji font loader."""
        size = max(1, int(size))
        if self.emoji_font_path and os.path.exists(self.emoji_font_path):
            try:
                return ImageFont.truetype(self.emoji_font_path, size)
            except Exception as e:
                logger.warning("Emoji font loading failed size=%s: %s", size, summarize_exception(e, label="emoji_font"))
        return None

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

    def _draw_shadow(self, draw, box, radius, color=(0, 0, 0, 60), offset=(6, 6)):
        x1, y1, x2, y2 = box
        off_x, off_y = offset
        draw.rounded_rectangle(
            [x1 + off_x, y1 + off_y, x2 + off_x, y2 + off_y],
            radius=radius, fill=color
        )

    def generate_advanced_quote(self, messages, output_path="quote.webp", **kwargs):
        """Safe High-Res Generator."""
        try:
            return self._internal_render(messages, output_path, **kwargs)
        except Exception as e:
            logger.error("Internal render failed: %s. Falling back to emergency mode.", summarize_exception(e, label="quote_render"))
            # Last resort: very basic image
            img = Image.new("RGBA", (512, 512), (30, 30, 30, 255))
            draw = ImageDraw.Draw(img)
            draw.text((20, 20), "Quote Generation Error", fill="white")
            img.save(output_path, "WEBP")
            return output_path

    def _internal_render(self, messages, output_path, **kwargs):
        scale_factor = kwargs.get('scale', 1.0)
        is_png = kwargs.get('is_png', False)
        custom_bg = kwargs.get('bg_color')
        show_reply = kwargs.get('show_reply', True)
        should_crop = kwargs.get('crop', True)
        
        rs = self.render_scale
        
        bubble_color = self._hex_to_rgba(custom_bg, 255)
        canvas_bg = (0, 0, 0, 0)
        
        padding = 24 * rs
        avatar_size = 80 * rs
        bubble_padding = 24 * rs
        msg_spacing = 20 * rs
        line_spacing = 10 * rs
        
        name_font = self._get_font(28 * rs, bold=True)
        text_font = self._get_font(27 * rs, bold=False)
        reply_font = self._get_font(20 * rs, bold=False)
        
        processed_msgs = []
        max_bubble_w = 0
        total_h = 0
        
        usable_w = 600 * rs
        dummy = Image.new("RGBA", (1, 1))
        d_draw = ImageDraw.Draw(dummy)
        
        for msg in messages:
            text = msg.get('text', '')
            name = msg.get('name', 'User')
            
            words = text.split()
            lines = []
            cur = []
            for w in words:
                test = " ".join(cur + [w])
                if d_draw.textbbox((0,0), test, font=text_font)[2] <= usable_w:
                    cur.append(w)
                else:
                    lines.append(" ".join(cur))
                    cur = [w]
            if cur: lines.append(" ".join(cur))
            
            this_w = d_draw.textbbox((0,0), name, font=name_font)[2]
            for l in lines:
                this_w = max(this_w, d_draw.textbbox((0,0), l, font=text_font)[2])
            
            reply_h = 0
            if msg.get('reply_text') and show_reply:
                reply_h = 70 * rs
                this_w = max(this_w, d_draw.textbbox((0,0), msg['reply_text'][:50], font=reply_font)[2] + 40*rs)

            bw = this_w + (bubble_padding * 2)
            max_bubble_w = max(max_bubble_w, bw)
            
            bh = (45 * rs) + (len(lines) * (30 * rs + line_spacing)) + (bubble_padding * 2)
            if reply_h: bh += reply_h
            
            processed_msgs.append({
                'lines': lines, 'name': name, 'avatar': msg.get('avatar_path'),
                'h': bh, 'w': bw, 'reply_text': msg.get('reply_text'),
                'reply_name': msg.get('reply_name'), 'reply_h': reply_h
            })
            total_h += bh + msg_spacing
            
        cw = int(padding + avatar_size + (20 * rs) + max_bubble_w + padding)
        ch = int(total_h + padding)
        
        img = Image.new("RGBA", (cw, ch), canvas_bg)
        draw = ImageDraw.Draw(img)
        
        y = padding
        bx = padding + avatar_size + (20 * rs)
        
        for m in processed_msgs:
            if m['avatar'] and os.path.exists(m['avatar']):
                try:
                    av = Image.open(m['avatar']).convert("RGBA").resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
                    mask = Image.new("L", (avatar_size, avatar_size), 0)
                    ImageDraw.Draw(mask).ellipse((0, 0, avatar_size, avatar_size), fill=255)
                    img.paste(av, (int(padding), int(y)), mask)
                except: pass
            
            box = [bx, y, bx + m['w'], y + m['h']]
            self._draw_shadow(draw, box, 35*rs, offset=(6*rs, 6*rs))
            draw.rounded_rectangle(box, radius=35*rs, fill=bubble_color)
            
            ty = y + bubble_padding
            if m['reply_text'] and show_reply:
                rb = [bx + 15*rs, ty, bx + m['w'] - 15*rs, ty + 60*rs]
                draw.rounded_rectangle(rb, radius=10*rs, fill=(255,255,255,15))
                draw.rectangle([rb[0], rb[1], rb[0]+6*rs, rb[3]], fill=self.name_color)
                draw.text((rb[0]+18*rs, rb[1]+8*rs), m['reply_name'], font=reply_font, fill=self.name_color)
                draw.text((rb[0]+18*rs, rb[1]+32*rs), m['reply_text'][:45]+"...", font=reply_font, fill=(220,220,220))
                ty += 80*rs
            
            draw.text((bx + bubble_padding, ty), m['name'], font=name_font, fill=self.name_color)
            ty += 48*rs
            for l in m['lines']:
                draw.text((bx + bubble_padding, ty), l, font=text_font, fill=self.text_color)
                ty += 30*rs + line_spacing
            y += m['h'] + msg_spacing
            
        if should_crop:
            bbox = img.getbbox()
            if bbox:
                m = 10*rs
                img = img.crop((max(0, bbox[0]-m), max(0, bbox[1]-m), min(img.width, bbox[2]+m), min(img.height, bbox[3]+m)))

        fw, fh = max(1, int(img.width/rs*scale_factor)), max(1, int(img.height/rs*scale_factor))
        img = img.resize((fw, fh), Image.Resampling.LANCZOS)
        
        if is_png: img.save(output_path, "PNG", optimize=True)
        else: img.save(output_path, "WEBP", quality=100, lossless=True)
        return output_path

import os
import re
import logging
from PIL import ImageFont, Image, ImageDraw
from src.security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)

EMOJI_NATIVE_SIZE = 109
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U0000FE00-\U0000FE0F"
    "\U0000200D"
    "\U000023CF-\U000023F3"
    "\U0000231A-\U0000231B"
    "\U00002934-\U00002935"
    "\U000025AA-\U000025FE"
    "\U00002B05-\U00002B07"
    "\U00002B1B-\U00002B1C"
    "\U00002B50\U00002B55"
    "\U00003030\U0000303D"
    "\U00003297\U00003299"
    "]+", re.UNICODE
)


class FontManager:
    def __init__(self):
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
        # Comprehensive fallback list â€” order matters (best coverage first)
        self.fallback_font_candidates = [
            "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansMath-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansSymbols-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansJavanese-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansGeorgian-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansArmenian-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:\\Windows\\Fonts\\seguisym.ttf",
            "C:\\Windows\\Fonts\\cambria.ttc",
        ]
        self.emoji_font_candidates = [
            "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
            "C:\\Windows\\Fonts\\seguiemj.ttf"
        ]
        
        self.font_path = self._find_first(self.font_candidates)
        self.font_regular_path = self._find_first(self.font_regular_candidates)
        self.emoji_font_path = self._find_first(self.emoji_font_candidates)
        self.fallback_font_paths = [p for p in self.fallback_font_candidates if os.path.exists(p)]

        logger.info("Quote fonts initialized bold=%s regular=%s fallback_count=%s", bool(self.font_path), bool(self.font_regular_path), len(self.fallback_font_paths))

        self._emoji_font_native = None
        if self.emoji_font_path:
            try:
                self._emoji_font_native = ImageFont.truetype(self.emoji_font_path, EMOJI_NATIVE_SIZE)
                logger.info("Quote emoji font loaded")
            except Exception as e:
                logger.warning("Quote emoji font failed: %s", summarize_exception(e, label="emoji_font"))

        self._fallback_cache = {}
        # Cache for "tofu" images per primary font size
        self._tofu_cache = {}

    def _find_first(self, candidates):
        for p in candidates:
            if os.path.exists(p):
                return p
        return None

    def get_font(self, size, bold=False):
        path = self.font_path if bold else self.font_regular_path
        size = max(1, int(size))
        if path:
            try:
                return ImageFont.truetype(path, size)
            except Exception as e:
                logger.warning("Font load failed size=%s: %s", size, summarize_exception(e, label="font"))
        return ImageFont.load_default()

    def _get_fallback_fonts(self, size):
        size = max(1, int(size))
        if size not in self._fallback_cache:
            fonts = []
            for path in self.fallback_font_paths:
                try:
                    fonts.append(ImageFont.truetype(path, size))
                except:
                    pass
            self._fallback_cache[size] = fonts
        return self._fallback_cache[size]

    def _font_can_render(self, font, char):
        """
        Check if a font can render a character by rendering it and comparing
        against a known-missing character (U+FFFF â€” "tofu").
        """
        try:
            # Create small canvas to test rendering
            # Size must be enough to capture the glyph
            size = 40
            img_char = Image.new("1", (size, size), 0)
            d_char = ImageDraw.Draw(img_char)
            d_char.text((size//2, size//2), char, font=font, fill=1, anchor="mm")
            
            # If nothing rendered (empty), it's missing (or space)
            bbox = img_char.getbbox()
            if not bbox and char.strip():
                return False
            
            # Cache the tofu image for this font instance
            font_id = id(font)
            if font_id not in self._tofu_cache:
                img_tofu = Image.new("1", (size, size), 0)
                d_tofu = ImageDraw.Draw(img_tofu)
                d_tofu.text((size//2, size//2), "\uFFFF", font=font, fill=1, anchor="mm")
                self._tofu_cache[font_id] = img_tofu.tobytes()

            # If char renders identical to tofu, font doesn't have it
            if img_char.tobytes() == self._tofu_cache[font_id]:
                return False
                
            return True
        except:
            return False

    def has_emoji(self, text):
        return bool(EMOJI_PATTERN.search(text))

    def _is_basic_char(self, char):
        """Only ASCII printable chars are guaranteed basic."""
        cp = ord(char)
        return 32 <= cp <= 126

    def render_text_with_fallback(self, draw, pos, text, primary_font, fill, size_px):
        """
        Renders text with automatic font fallback.
        """
        if not text:
            return
        
        # Fast path
        if all(self._is_basic_char(c) for c in text):
            draw.text(pos, text, font=primary_font, fill=fill)
            return
        
        fallback_fonts = self._get_fallback_fonts(size_px)
        x, y = pos
        
        segments = []
        current_text = ""
        current_font = primary_font
        
        for char in text:
            target_font = primary_font
            
            # If basic, use primary
            if self._is_basic_char(char):
                target_font = primary_font
            else:
                # Check if primary can render
                if self._font_can_render(primary_font, char):
                    target_font = primary_font
                else:
                    # Find a fallback
                    found = False
                    for fb in fallback_fonts:
                        if self._font_can_render(fb, char):
                            target_font = fb
                            found = True
                            break
                    if not found:
                        target_font = primary_font # Give up
            
            if target_font is current_font:
                current_text += char
            else:
                if current_text:
                    segments.append((current_text, current_font))
                current_text = char
                current_font = target_font
        
        if current_text:
            segments.append((current_text, current_font))
        
        for seg_text, seg_font in segments:
            draw.text((x, y), seg_text, font=seg_font, fill=fill)
            bbox = draw.textbbox((0, 0), seg_text, font=seg_font)
            x += bbox[2] - bbox[0]

    def render_text_with_emoji(self, draw, pos, text, font, fill, target_h):
        """
        Renders text with inline emoji compositing + font fallback.
        """
        if not self._emoji_font_native or not self.has_emoji(text):
            font_size = font.size if hasattr(font, 'size') else target_h
            self.render_text_with_fallback(draw, pos, text, font, fill, font_size)
            return

        segments = []
        last_end = 0
        for match in EMOJI_PATTERN.finditer(text):
            if match.start() > last_end:
                segments.append((text[last_end:match.start()], False))
            segments.append((match.group(), True))
            last_end = match.end()
        if last_end < len(text):
            segments.append((text[last_end:], False))

        x, y = pos
        canvas = draw._image
        font_size = font.size if hasattr(font, 'size') else target_h

        for seg_text, is_emoji in segments:
            if not is_emoji:
                self.render_text_with_fallback(draw, (x, y), seg_text, font, fill, font_size)
                bbox = draw.textbbox((0, 0), seg_text, font=font)
                x += bbox[2] - bbox[0]
            else:
                try:
                    emoji_img = Image.new("RGBA", (EMOJI_NATIVE_SIZE + 20, EMOJI_NATIVE_SIZE + 20), (0, 0, 0, 0))
                    emoji_draw = ImageDraw.Draw(emoji_img)
                    emoji_draw.text((0, 0), seg_text, font=self._emoji_font_native, embedded_color=True)
                    
                    ebbox = emoji_img.getbbox()
                    if ebbox:
                        emoji_img = emoji_img.crop(ebbox)
                    
                    emoji_h = max(1, int(target_h))
                    ratio = emoji_h / max(1, emoji_img.height)
                    emoji_w = max(1, int(emoji_img.width * ratio))
                    emoji_img = emoji_img.resize((emoji_w, emoji_h), Image.Resampling.LANCZOS)
                    
                    canvas.paste(emoji_img, (int(x), int(y)), emoji_img)
                    x += emoji_w + 4
                except Exception:
                    draw.text((x, y), seg_text, font=font, fill=fill)
                    bbox = draw.textbbox((0, 0), seg_text, font=font)
                    x += bbox[2] - bbox[0]

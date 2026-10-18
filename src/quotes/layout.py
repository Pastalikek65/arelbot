from PIL import Image, ImageDraw

class LayoutEngine:
    def __init__(self, rs=3):
        self.rs = rs # Render scale (supersampling)
        self.padding = 24 * rs
        self.avatar_size = 80 * rs
        self.bubble_padding = 24 * rs
        self.msg_spacing = 20 * rs
        self.line_spacing = 10 * rs
        self.max_bubble_w_ratio = 0.7 # Max 70% of canvas width

    def wrap_text(self, text, font, max_width):
        """Smart text wrapping for high-res rendering."""
        if not text: return []
        
        dummy = Image.new("RGBA", (1, 1))
        draw = ImageDraw.Draw(dummy)
        
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            test_line = " ".join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                    current_line = [word]
                else:
                    # Single word too long
                    lines.append(word)
                    current_line = []
                    
        if current_line:
            lines.append(" ".join(current_line))
            
        return lines

    def calculate_metrics(self, messages, name_font, text_font, reply_font, canvas_w):
        """Calculates all positions and sizes before rendering."""
        usable_text_w = (canvas_w - self.padding * 2 - self.avatar_size - 20 * self.rs) * 0.95
        
        processed_msgs = []
        total_h = self.padding
        max_bubble_w = 0
        
        dummy = Image.new("RGBA", (1, 1))
        draw = ImageDraw.Draw(dummy)

        for msg in messages:
            text = msg.get('text', '')
            name = msg.get('name', 'User')
            
            lines = self.wrap_text(text, text_font, usable_text_w)
            
            # Find widest element in bubble
            name_bbox = draw.textbbox((0, 0), name, font=name_font)
            bubble_content_w = name_bbox[2] - name_bbox[0]
            
            for line in lines:
                l_bbox = draw.textbbox((0, 0), line, font=text_font)
                bubble_content_w = max(bubble_content_w, l_bbox[2] - l_bbox[0])
            
            reply_h = 0
            if msg.get('reply_text'):
                reply_h = 80 * self.rs
                r_text_w = draw.textbbox((0, 0), msg['reply_text'][:60], font=reply_font)[2]
                bubble_content_w = max(bubble_content_w, r_text_w + 40 * self.rs)

            bw = bubble_content_w + (self.bubble_padding * 2)
            max_bubble_w = max(max_bubble_w, bw)
            
            bh = (45 * self.rs) + (len(lines) * (30 * self.rs + self.line_spacing)) + (self.bubble_padding * 2)
            if reply_h: bh += reply_h + 10 * self.rs
            
            processed_msgs.append({
                'lines': lines,
                'name': name,
                'avatar': msg.get('avatar_path'),
                'h': bh,
                'w': bw,
                'reply_text': msg.get('reply_text'),
                'reply_name': msg.get('reply_name'),
                'reply_h': reply_h
            })
            total_h += bh + self.msg_spacing
            
        return processed_msgs, total_h + self.padding, max_bubble_w

"""Constrain model-generated markup before it reaches Telegram HTML parsing."""

from html import escape
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

_ALLOWED = {"b", "strong", "i", "em", "u", "s", "code", "pre", "blockquote", "tg-spoiler"}


class _TelegramHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []
        self._tags = []

    def handle_data(self, data):
        self.parts.append(escape(data, quote=False))

    def handle_entityref(self, name):
        self.parts.append(f"&amp;{name};")

    def handle_charref(self, name):
        self.parts.append(f"&amp;#{name};")

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _ALLOWED:
            self.parts.append(f"<{tag}>")
            self._tags.append(tag)
        elif tag == "a":
            href = next((value for key, value in attrs if key.lower() == "href"), None)
            scheme = urlsplit(href.strip()).scheme.lower() if href else ""
            if href and scheme in {"http", "https", "tg"}:
                self.parts.append(f'<a href="{escape(href, quote=True)}">')
                self._tags.append("a")
            else:
                self._tags.append(None)
        else:
            self._tags.append(None)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        emitted = self._tags.pop() if self._tags else None
        if emitted == tag:
            self.parts.append(f"</{tag}>")

    def handle_comment(self, data):
        self.parts.append(escape(f"<!--{data}-->", quote=False))


def sanitize_html(text: str) -> str:
    parser = _TelegramHTMLParser()
    try:
        parser.feed(text or "")
        parser.close()
    except Exception:
        return escape(text or "", quote=False)
    # Telegram rejects malformed/unclosed markup. Close only tags that were
    # emitted by the parser, preserving the generated text safely.
    for tag in reversed([tag for tag in parser._tags if tag]):
        parser.parts.append(f"</{tag}>")
    return "".join(parser.parts)


_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]{1,240})\]\((https?://[^\s)<>]{1,1800}|tg://[^\s)<>]{1,1800})\)")
_MARKDOWN_CODE_RE = re.compile(r"```(?:[A-Za-z0-9_+-]+)?\n?(.*?)```", re.DOTALL)
_MARKDOWN_INLINE_CODE_RE = re.compile(r"`([^`\n]{1,400})`")
_MARKDOWN_STRONG_RE = re.compile(r"\*\*(\S(?:.*?\S)?)\*\*|__(\S(?:.*?\S)?)__", re.DOTALL)
_MARKDOWN_EM_RE = re.compile(r"(?<!\*)\*(?!\s)([^*\n]{1,400}?)(?<!\s)\*(?!\*)")
_MARKDOWN_TOKEN_RE = re.compile("\\ue000(\\d+)\\ue001")

# These envelopes are transport-only metadata used to keep group replies
# attached to the correct Telegram message. They must never reach a chat,
# including when a model copies part of its context into the answer.
_INTERNAL_ATTRIBUTION_TAG_RE = re.compile(
    r"\[\s*(?:arel_)?(?:reply_to_user_id|telegram_user_id)\s*=\s*-?\d+"
    r"(?:\s+(?:message_id|reply_to_message_id)\s*=\s*-?\d+)?\s*\]\s*",
    re.IGNORECASE,
)
_INTERNAL_ATTRIBUTION_FIELD_RE = re.compile(
    r"(?<![\w])(?:arel_reply_to_user_id|telegram_user_id|target_user_id|"
    r"target_message_id|reply_to_message_id|telegram_message_id|message_id)"
    r"\s*[:=]\s*-?\d+\b\s*",
    re.IGNORECASE,
)


def strip_internal_attribution(text: str | None) -> str:
    """Remove transport-only Telegram identity metadata from model output.

    Telegram IDs remain available to the delivery ledger and persistence
    layer, but are never part of the user-facing response. The patterns are
    deliberately narrow so ordinary prose and numeric content stay intact.
    """

    source = str(text or "")
    source = _INTERNAL_ATTRIBUTION_TAG_RE.sub("", source)
    return _INTERNAL_ATTRIBUTION_FIELD_RE.sub("", source)


def render_model_html(text: str | None) -> str:
    """Render the small Markdown subset commonly emitted by AI models.

    Telegram is called with ``parse_mode=HTML`` throughout the bot. Models,
    however, frequently return Markdown emphasis, so passing their output
    straight to :func:`sanitize_html` leaves markers such as ``**bold**``
    visible to users. This adapter converts only bounded, safe Markdown and
    sends the result through the existing HTML sanitizer. Code spans are
    protected before conversion so their contents remain literal.
    """

    source = strip_internal_attribution(text)
    protected: dict[str, str] = {}

    def stash(value: str) -> str:
        token = f"\ue000{len(protected)}\ue001"
        protected[token] = value
        return token

    def fenced(match: re.Match[str]) -> str:
        return stash(f"<pre><code>{escape(match.group(1) or '', quote=False)}</code></pre>")

    def inline_code(match: re.Match[str]) -> str:
        return stash(f"<code>{escape(match.group(1) or '', quote=False)}</code>")

    rendered = _MARKDOWN_CODE_RE.sub(fenced, source)
    rendered = _MARKDOWN_INLINE_CODE_RE.sub(inline_code, rendered)

    def link(match: re.Match[str]) -> str:
        label = escape(match.group(1), quote=False)
        href = escape(match.group(2), quote=True)
        return f'<a href="{href}">{label}</a>'

    rendered = _MARKDOWN_LINK_RE.sub(link, rendered)

    def strong(match: re.Match[str]) -> str:
        value = match.group(1) or match.group(2) or ""
        return f"<b>{value}</b>"

    rendered = _MARKDOWN_STRONG_RE.sub(strong, rendered)
    rendered = _MARKDOWN_EM_RE.sub(r"<i>\1</i>", rendered)
    sanitized = sanitize_html(rendered)
    for token, value in protected.items():
        sanitized = sanitized.replace(token, value)
    # A malformed model response must never leak private-use token markers.
    return _MARKDOWN_TOKEN_RE.sub("", sanitized)


def render_model_plain_text(text: str | None) -> str:
    """Return a readable fallback when Telegram rejects HTML entities.

    A raw model response may contain Markdown markers which are harmless in
    HTML mode but confusing when Telegram falls back to plain text. Strip the
    bounded emphasis/code delimiters while preserving the user's content.
    """
    source = strip_internal_attribution(text)
    source = _MARKDOWN_CODE_RE.sub(lambda match: match.group(1) or "", source)
    source = _MARKDOWN_INLINE_CODE_RE.sub(lambda match: match.group(1) or "", source)
    source = re.sub(r"\*\*|__", "", source)
    source = re.sub(r"(?<!\*)\*(?!\*)", "", source)
    return source

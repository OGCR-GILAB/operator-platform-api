import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

CODE = re.compile(r"`([^`]+)`")
ISSUE = re.compile(r"(?<![\w/])#(\d+)\b")
ISSUES_URL = "https://codeberg.org/OGCR/operator-platform/issues/"


def _inline(text: str) -> str:
    html = CODE.sub(r"<code>\1</code>", escape(text))
    return ISSUE.sub(rf'<a href="{ISSUES_URL}\1" target="_blank" rel="noopener">#\1</a>', html)


@register.filter
def notes(text: str) -> str:
    """Plain text with `- ` bullet lines, `code` and #issue links -> safe HTML."""
    blocks: list[str] = []
    bullets: list[str] = []

    def flush() -> None:
        if bullets:
            items = "".join(f"<li>{b}</li>" for b in bullets)
            blocks.append(f"<ul>{items}</ul>")
            bullets.clear()

    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            bullets.append(_inline(stripped[2:]))
        elif stripped:
            flush()
            blocks.append(f"<p>{_inline(stripped)}</p>")
        else:
            flush()
    flush()
    return mark_safe("".join(blocks))


@register.filter
def issue_links(text: str) -> str:
    return mark_safe(_inline(text or ""))

"""Turn a plain-text email into a dark HTML version with the Lumawen logo.

Every email is written as plain text (that part is always sent too). At send time this builds
the HTML alternative from it: blank lines make paragraphs, links become clickable, a link alone
on its own line becomes a button, and anything after a "--" line is the small footer. The logo
travels inside the email (an inline image), so it shows without loading anything remote.
"""

import html
import re
from functools import cache
from importlib.resources import files

LOGO_CID = "lumawen-logo@lumawen.com"
FOOTER_MARK = "\n\n--\n"
URL_RE = re.compile(r"https?://[^\s<>\"']+")

# Button labels for the emails whose main job is one link. Anything else says "Open link".
BUTTON_LABELS = {
    "confirm": "Confirm my email",
    "list_confirm": "Confirm my subscription",
    "reset": "Choose a new password",
    "already_registered": "Reset my password",
}

BG = "#000000"
TEXT = "#ffffff"
MUTED = "#a3a3a3"
RULE = "#262626"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


@cache
def logo_png() -> bytes:
    return files("lumawen_api").joinpath("assets/email-logo.png").read_bytes()


def _linkify(text: str, color: str = TEXT) -> str:
    """Escape text and make every URL in it a link."""
    out, last = [], 0
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?)")
        end = m.start() + len(url)
        out.append(html.escape(text[last : m.start()]))
        safe = html.escape(url, quote=True)
        out.append(f'<a href="{safe}" style="color:{color};text-decoration:underline">{safe}</a>')
        last = end
    out.append(html.escape(text[last:]))
    return "".join(out).replace("\n", "<br>")


def _button(url: str, label: str) -> str:
    safe = html.escape(url, quote=True)
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:8px 0 24px"><tr>'
        f'<td bgcolor="{TEXT}" style="border-radius:999px">'
        f'<a href="{safe}" style="display:inline-block;padding:13px 26px;font-family:{FONT};'
        f'font-size:16px;font-weight:700;color:{BG};text-decoration:none;border-radius:999px">'
        f"{html.escape(label)}</a></td></tr></table>"
        f'<p style="margin:0 0 20px;font-size:12px;line-height:1.5;color:{MUTED};'
        'word-break:break-all">Button not working? Copy this link:<br>'
        f'<a href="{safe}" style="color:{MUTED}">{safe}</a></p>'
    )


def _paragraphs(text: str, label: str) -> str:
    parts = []
    for block in re.split(r"\n\s*\n", text.strip()):
        block = block.strip()
        if not block:
            continue
        if URL_RE.fullmatch(block):
            parts.append(_button(block, label))
        else:
            parts.append(
                f'<p style="margin:0 0 20px;font-size:16px;line-height:1.6;color:{TEXT}">'
                f"{_linkify(block)}</p>"
            )
    return "\n".join(parts)


def render_html(body_text: str, subject: str, kind: str = "") -> str:
    main, _, footer = body_text.partition(FOOTER_MARK)
    content = _paragraphs(main, BUTTON_LABELS.get(kind, "Open link"))
    footer_html = ""
    if footer.strip():
        footer_html = (
            f'<tr><td style="padding:24px 32px 0;border-top:1px solid {RULE};font-family:{FONT};'
            f'font-size:12px;line-height:1.6;color:{MUTED}">'
            f"{_linkify(footer.strip(), MUTED)}</td></tr>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<title>{html.escape(subject)}</title>
<style>:root{{color-scheme:dark;supported-color-schemes:dark}}body{{margin:0;padding:0}}</style>
</head>
<body bgcolor="{BG}" style="margin:0;padding:0;background:{BG};color:{TEXT}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
 bgcolor="{BG}" style="background:{BG}">
<tr><td align="center" style="padding:40px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
 style="max-width:560px">
<tr><td align="center" style="padding:0 32px 36px">
<a href="https://lumawen.com" style="text-decoration:none">
<img src="cid:{LOGO_CID}" width="80" height="101" alt="lumawen"
 style="display:block;border:0;width:80px;height:101px;color:{TEXT};font-family:{FONT};
 font-size:22px;font-weight:700"></a>
</td></tr>
<tr><td style="padding:0 32px 8px;font-family:{FONT}">
{content}
</td></tr>
{footer_html}
</table>
</td></tr>
</table>
</body>
</html>
"""

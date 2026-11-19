"""Shared DevZ-Go transactional email chrome (Outlook-safe tables + inline CSS)."""

from __future__ import annotations

from html import escape

from app.core.public_urls import backend_base, frontend_base


BRAND = {
    "maroon": "#721c2a",
    "ivory": "#f8f6f0",
    "ink": "#3d0a14",
    "muted": "#78716c",
    "card": "#ffffff",
    "border": "#f8d0d7",
    "gold": "#fced96",
    "cream": "#fffdf7",
}


def logo_url() -> str:
    return f"{backend_base()}/static/brand/email-logo.png"


def site_url(path: str = "/") -> str:
    base = frontend_base().rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def render_email(
    *,
    preheader: str,
    title: str,
    body_html: str,
    cta_label: str | None = None,
    cta_url: str | None = None,
    footer_html: str | None = None,
) -> str:
    logo = escape(logo_url(), quote=True)
    pre = escape(preheader)
    heading = escape(title)
    cta = ""
    if cta_label and cta_url:
        cta = f"""
          <tr>
            <td align="center" style="padding:22px 28px 8px;">
              <a href="{escape(cta_url, quote=True)}" style="display:inline-block;background:{BRAND['maroon']};color:{BRAND['cream']};text-decoration:none;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:15px;font-weight:700;padding:14px 28px;border-radius:14px;">
                {escape(cta_label)}
              </a>
            </td>
          </tr>"""
    foot = footer_html or (
        f'<p style="margin:0;">DevZ-Go · <a href="{escape(site_url("/"), quote=True)}" style="color:{BRAND["maroon"]};">devzgo.com</a></p>'
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{heading}</title>
</head>
<body style="margin:0;padding:0;background:{BRAND['ivory']};">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{pre}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BRAND['ivory']};padding:32px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:{BRAND['card']};border-radius:24px;overflow:hidden;border:1px solid {BRAND['border']};">
          <tr>
            <td align="center" style="background:{BRAND['maroon']};padding:28px 24px 22px;">
              <img src="{logo}" width="96" height="96" alt="DevZ-Go" style="display:block;width:96px;height:96px;border:0;border-radius:22px;" />
              <p style="margin:14px 0 0;font-family:Georgia,'Times New Roman',serif;font-size:28px;line-height:1.2;color:{BRAND['cream']};">DevZ-Go</p>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 28px 8px;font-family:Georgia,'Times New Roman',serif;color:{BRAND['ink']};">
              <p style="margin:0 0 12px;font-size:22px;line-height:1.3;">{heading}</p>
              <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:15px;line-height:1.6;color:#44403c;">
                {body_html}
              </div>
            </td>
          </tr>
          {cta}
          <tr>
            <td style="padding:16px 28px 28px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:13px;line-height:1.6;color:{BRAND['muted']};">
              {foot}
            </td>
          </tr>
        </table>
        <p style="margin:16px 0 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:12px;color:#a8a29e;">
          DevZ-Go · built for developers
        </p>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def render_text(*, title: str, lines: list[str], cta_url: str | None = None) -> str:
    parts = [title, ""] + lines
    if cta_url:
        parts += ["", cta_url]
    parts += ["", "DevZ-Go · built for developers"]
    return "\n".join(parts)

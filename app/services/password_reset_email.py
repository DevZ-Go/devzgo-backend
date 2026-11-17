"""HTML for the password-reset email. The reset token stays in the link only."""

from html import escape

from app.core.public_urls import backend_base


def password_reset_html(username: str, link: str) -> str:
    name = escape(username)
    href = escape(link, quote=True)
    logo = escape(f"{backend_base()}/static/brand/email-logo.png", quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Reset your DevZ-Go password</title>
</head>
<body style="margin:0;padding:0;background:#f8f6f0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f8f6f0;padding:32px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:24px;overflow:hidden;border:1px solid #f8d0d7;">
          <tr>
            <td align="center" style="background:#721c2a;padding:28px 24px 22px;">
              <img src="{logo}" width="96" height="96" alt="DevZ-Go" style="display:block;width:96px;height:96px;border:0;border-radius:22px;" />
              <p style="margin:14px 0 0;font-family:Georgia,'Times New Roman',serif;font-size:28px;line-height:1.2;color:#fffdf7;">DevZ-Go</p>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 28px 8px;font-family:Georgia,'Times New Roman',serif;color:#3d0a14;">
              <p style="margin:0 0 12px;font-size:22px;line-height:1.3;">Reset your password</p>
              <p style="margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:15px;line-height:1.6;color:#44403c;">
                Hi {name}, we received a request to reset the password for your DevZ-Go account.
                This link expires in 30 minutes and can be used once.
              </p>
            </td>
          </tr>
          <tr>
            <td align="center" style="padding:22px 28px 8px;">
              <a href="{href}" style="display:inline-block;background:#721c2a;color:#fffdf7;text-decoration:none;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:15px;font-weight:700;padding:14px 28px;border-radius:14px;">
                Reset password
              </a>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 28px 28px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:13px;line-height:1.6;color:#78716c;">
              <p style="margin:0 0 12px;">If the button does not open, paste this link into your browser:</p>
              <p style="margin:0 0 16px;word-break:break-all;"><a href="{href}" style="color:#8b263e;">{href}</a></p>
              <p style="margin:0;">If you did not ask for this, you can ignore this email. Your password will stay the same.</p>
            </td>
          </tr>
        </table>
        <p style="margin:16px 0 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:12px;color:#a8a29e;">
          DevZ-Go · made for developers
        </p>
      </td>
    </tr>
  </table>
</body>
</html>
"""

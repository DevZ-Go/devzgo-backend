"""Render branded transactional emails. Never put secrets or OTP plaintext in logs."""

from __future__ import annotations

from html import escape
from typing import Any

from app.services.email_templates.base import render_email, render_text, site_url


def _p(text: str) -> str:
    return f'<p style="margin:0 0 12px;">{escape(text)}</p>'


def _code_block(code: str) -> str:
    return (
        f'<p style="margin:16px 0;text-align:center;font-size:32px;letter-spacing:0.35em;'
        f'font-weight:700;color:#721c2a;font-family:Georgia,serif;">{escape(code)}</p>'
    )


def render_template(name: str, ctx: dict[str, Any]) -> tuple[str, str, str]:
    """Return (subject, html, text)."""
    username = str(ctx.get("username") or "there")
    if name == "verification_otp":
        code = str(ctx["code"])
        subject = "Your DevZ-Go verification code"
        html = render_email(
            preheader=f"Your code is {code}. It expires in 10 minutes.",
            title="Verify your email",
            body_html=_p(f"Hi {username}, use this code to finish creating your DevZ-Go account.")
            + _code_block(code)
            + _p("This code expires in 10 minutes and can be used once. If you did not start a signup, you can ignore this email."),
        )
        text = render_text(title=subject, lines=[f"Hi {username}, your code is {code}. It expires in 10 minutes."])
        return subject, html, text

    if name == "welcome":
        subject = "Welcome to DevZ-Go"
        html = render_email(
            preheader="Your account is ready.",
            title="Welcome to DevZ-Go",
            body_html=_p(f"Hi {username}, your email is verified and your account is ready.")
            + _p("Sign in to showcase projects, connect with developers, and build in public."),
            cta_label="Sign in",
            cta_url=site_url("/login"),
        )
        text = render_text(title=subject, lines=[f"Hi {username}, welcome to DevZ-Go.", site_url("/login")])
        return subject, html, text

    if name == "password_reset":
        link = str(ctx["link"])
        subject = "Reset your DevZ-Go password"
        html = render_email(
            preheader="Reset link expires in 30 minutes.",
            title="Reset your password",
            body_html=_p(f"Hi {username}, we received a request to reset the password for your DevZ-Go account.")
            + _p("This link expires in 30 minutes and can be used once.")
            + f'<p style="margin:0 0 12px;word-break:break-all;"><a href="{escape(link, quote=True)}" style="color:#8b263e;">{escape(link)}</a></p>'
            + _p("If you did not ask for this, you can ignore this email."),
            cta_label="Reset password",
            cta_url=link,
        )
        text = render_text(title=subject, lines=[f"Hi {username}, reset your password:", link])
        return subject, html, text

    if name == "password_changed":
        subject = "Your DevZ-Go password was changed"
        html = render_email(
            preheader="If this was not you, reset your password.",
            title="Password changed",
            body_html=_p(f"Hi {username}, the password on your DevZ-Go account was just changed.")
            + _p("If this was not you, reset your password right away."),
            cta_label="Reset password",
            cta_url=site_url("/forgot-password"),
        )
        text = render_text(title=subject, lines=[f"Hi {username}, your password was changed."])
        return subject, html, text

    if name == "email_changed":
        subject = "Your DevZ-Go email was changed"
        html = render_email(
            preheader="Your account email was updated.",
            title="Email address updated",
            body_html=_p(f"Hi {username}, the email on your DevZ-Go account is now {escape(str(ctx.get('new_email') or ''))}.")
            + _p("If you did not make this change, contact support and secure your account."),
        )
        text = render_text(title=subject, lines=[f"Hi {username}, your email was changed."])
        return subject, html, text

    if name == "account_paused":
        subject = "Your DevZ-Go account is paused"
        html = render_email(
            preheader="Sign in again when you are ready to return.",
            title="Account paused",
            body_html=_p(f"Hi {username}, your DevZ-Go account is paused and hidden until you sign in again."),
            cta_label="Sign in",
            cta_url=site_url("/login"),
        )
        text = render_text(title=subject, lines=[f"Hi {username}, your account is paused."])
        return subject, html, text

    if name == "account_restored":
        subject = "Your DevZ-Go account is active again"
        html = render_email(
            preheader="Welcome back.",
            title="Account restored",
            body_html=_p(f"Hi {username}, your DevZ-Go account is active again."),
            cta_label="Open DevZ-Go",
            cta_url=site_url("/home"),
        )
        text = render_text(title=subject, lines=[f"Hi {username}, your account is active again."])
        return subject, html, text

    if name == "account_deletion_requested":
        when = str(ctx.get("delete_after") or "30 days")
        subject = "DevZ-Go account deletion scheduled"
        html = render_email(
            preheader="You can cancel by signing in before the deadline.",
            title="Deletion scheduled",
            body_html=_p(f"Hi {username}, your DevZ-Go account is scheduled for permanent deletion.")
            + _p(f"Deletion completes on {escape(when)}. Sign in before then to cancel."),
            cta_label="Cancel by signing in",
            cta_url=site_url("/login"),
        )
        text = render_text(title=subject, lines=[f"Hi {username}, deletion is scheduled for {when}."])
        return subject, html, text

    if name == "account_deleted":
        subject = "Your DevZ-Go account has been deleted"
        html = render_email(
            preheader="This account has been permanently removed.",
            title="Account deleted",
            body_html=_p(f"Hi {username}, your DevZ-Go account has been permanently deleted."),
        )
        text = render_text(title=subject, lines=[f"Hi {username}, your account has been deleted."])
        return subject, html, text

    if name == "connection_request":
        from_name = escape(str(ctx.get("from_name") or "Someone"))
        subject = f"{ctx.get('from_name') or 'Someone'} wants to connect on DevZ-Go"
        html = render_email(
            preheader="Review the connection request.",
            title="New connection request",
            body_html=_p(f"Hi {username}, {from_name} sent you a connection request on DevZ-Go."),
            cta_label="Open requests",
            cta_url=site_url("/network?tab=requests"),
            footer_html=_optional_unsub(ctx),
        )
        text = render_text(title=subject, lines=[f"{from_name} wants to connect.", site_url("/network?tab=requests")])
        return subject, html, text

    if name == "connection_milestone":
        count = int(ctx.get("count") or 0)
        subject = f"You reached {count} connections on DevZ-Go"
        html = render_email(
            preheader=f"You now have {count} accepted connections.",
            title="Connection milestone",
            body_html=_p(f"Hi {username}, you now have {count} accepted connections on DevZ-Go. Nice work building your network."),
            cta_label="See connections",
            cta_url=site_url("/network"),
            footer_html=_optional_unsub(ctx),
        )
        text = render_text(title=subject, lines=[f"Hi {username}, you reached {count} connections."])
        return subject, html, text

    if name == "unread_messages_digest":
        n = int(ctx.get("unread_count") or 0)
        subject = f"You have {n} unread message{'s' if n != 1 else ''} on DevZ-Go"
        html = render_email(
            preheader="Catch up when you have a moment.",
            title="Unread messages",
            body_html=_p(f"Hi {username}, you have {n} unread message{'s' if n != 1 else ''} waiting on DevZ-Go."),
            cta_label="Open messages",
            cta_url=site_url("/messages"),
            footer_html=_optional_unsub(ctx),
        )
        text = render_text(title=subject, lines=[f"Hi {username}, you have {n} unread messages."])
        return subject, html, text

    if name == "collaboration_invite":
        project = escape(str(ctx.get("project_title") or "a project"))
        subject = "Collaboration invitation on DevZ-Go"
        html = render_email(
            preheader="You were invited to collaborate.",
            title="Collaboration invitation",
            body_html=_p(f"Hi {username}, you were invited to collaborate on {project}."),
            cta_label="View invitation",
            cta_url=str(ctx.get("link") or site_url("/network?tab=collaborations")),
            footer_html=_optional_unsub(ctx),
        )
        text = render_text(title=subject, lines=[f"Hi {username}, collaboration invite for {ctx.get('project_title')}."])
        return subject, html, text

    if name == "project_update":
        project = escape(str(ctx.get("project_title") or "your project"))
        subject = f"Update on {ctx.get('project_title') or 'your project'}"
        html = render_email(
            preheader="A project you follow has an update.",
            title="Project update",
            body_html=_p(f"Hi {username}, there is a new update on {project}.")
            + _p(escape(str(ctx.get("summary") or ""))),
            cta_label="Open project",
            cta_url=str(ctx.get("link") or site_url("/explore")),
            footer_html=_optional_unsub(ctx),
        )
        text = render_text(title=subject, lines=[str(ctx.get("summary") or "")])
        return subject, html, text

    if name == "recruiter_opportunity":
        subject = str(ctx.get("subject") or "A recruiter opportunity on DevZ-Go")
        html = render_email(
            preheader="An opportunity matching your profile.",
            title="Opportunity",
            body_html=_p(f"Hi {username},") + _p(escape(str(ctx.get("body") or "You have a new opportunity on DevZ-Go."))),
            cta_label="Open",
            cta_url=str(ctx.get("link") or site_url("/assessments")),
            footer_html=_optional_unsub(ctx),
        )
        text = render_text(title=subject, lines=[str(ctx.get("body") or "")])
        return subject, html, text

    if name == "weekly_summary":
        subject = "Your weekly DevZ-Go summary"
        html = render_email(
            preheader="A quiet look at your week.",
            title="Weekly summary",
            body_html=_p(f"Hi {username}, here is your week on DevZ-Go.")
            + _p(escape(str(ctx.get("summary") or ""))),
            cta_label="Open DevZ-Go",
            cta_url=site_url("/home"),
            footer_html=_optional_unsub(ctx),
        )
        text = render_text(title=subject, lines=[str(ctx.get("summary") or "")])
        return subject, html, text

    raise ValueError(f"Unknown email template: {name}")


def _optional_unsub(ctx: dict[str, Any]) -> str:
    url = ctx.get("unsubscribe_url")
    if not url:
        return f'<p style="margin:0;">DevZ-Go · <a href="{escape(site_url("/"), quote=True)}" style="color:#721c2a;">devzgo.com</a></p>'
    return (
        f'<p style="margin:0 0 8px;">DevZ-Go</p>'
        f'<p style="margin:0;"><a href="{escape(str(url), quote=True)}" style="color:#78716c;">Unsubscribe from this kind of email</a></p>'
    )

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class EmailConfig:
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    from_email: str
    to_emails: list[str]
    use_tls: bool = True

    @classmethod
    def from_env(cls) -> "EmailConfig":
        smtp_host = os.getenv("REPORT_SMTP_HOST", "").strip()
        smtp_port = int(os.getenv("REPORT_SMTP_PORT", "587").strip())
        smtp_username = os.getenv("REPORT_SMTP_USERNAME", "").strip()
        smtp_password = os.getenv("REPORT_SMTP_PASSWORD", "").strip()
        from_email = os.getenv("REPORT_FROM_EMAIL", "").strip()
        to_emails = [item.strip() for item in os.getenv("REPORT_TO_EMAILS", "").split(",") if item.strip()]
        use_tls = os.getenv("REPORT_SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes", "on"}

        missing = [
            name
            for name, value in (
                ("REPORT_SMTP_HOST", smtp_host),
                ("REPORT_SMTP_USERNAME", smtp_username),
                ("REPORT_SMTP_PASSWORD", smtp_password),
                ("REPORT_FROM_EMAIL", from_email),
                ("REPORT_TO_EMAILS", ",".join(to_emails)),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing email configuration: {', '.join(missing)}")

        return cls(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            from_email=from_email,
            to_emails=to_emails,
            use_tls=use_tls,
        )


def send_report_email(
    config: EmailConfig,
    subject: str,
    plain_body: str,
    html_body: str,
    attachments: Iterable[Path],
) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.from_email
    message["To"] = ", ".join(config.to_emails)
    message.set_content(plain_body)
    message.add_alternative(html_body, subtype="html")

    for attachment_path in attachments:
        path = Path(attachment_path)
        if not path.exists():
            continue
        if path.suffix.lower() == ".html":
            mime_type = ("text", "html")
        elif path.suffix.lower() == ".csv":
            mime_type = ("text", "csv")
        elif path.suffix.lower() == ".pdf":
            mime_type = ("application", "pdf")
        else:
            mime_type = ("application", "octet-stream")
        message.add_attachment(
            path.read_bytes(),
            maintype=mime_type[0],
            subtype=mime_type[1],
            filename=path.name,
        )

    with smtplib.SMTP(config.smtp_host, config.smtp_port) as smtp:
        if config.use_tls:
            smtp.starttls()
        smtp.login(config.smtp_username, config.smtp_password)
        smtp.send_message(message)

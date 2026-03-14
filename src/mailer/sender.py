"""Email delivery via SendGrid."""
import sendgrid
from sendgrid.helpers.mail import Mail
from src.config import SENDGRID_API_KEY, TO_EMAIL, FROM_EMAIL


def send_email(subject: str, html_body: str):
    sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=TO_EMAIL,
        subject=subject,
        html_content=html_body,
    )
    response = sg.send(message)
    print(f"Email sent: {response.status_code}")
    return response

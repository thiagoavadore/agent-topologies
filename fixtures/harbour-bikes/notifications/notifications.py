"""Email sending for booking confirmations, receipts and reminders; Celery tasks call send_email."""

import os
import smtplib

SMTP_HOST = "smtp.harbourbikes.example"
SMTP_USER = "notifications"


def send_email(message):
    server = smtplib.SMTP(SMTP_HOST, 587)
    server.starttls()
    server.login(SMTP_USER, os.environ["SMTP_PASSWORD"])
    server.send_message(message)
    server.quit()

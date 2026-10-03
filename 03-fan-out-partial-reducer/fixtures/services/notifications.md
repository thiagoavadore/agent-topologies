# notifications

Owner: Team Growth (growth@harbourbikes.example)
Runtime: Python 3.12, Celery workers, 3 replicas across two zones.

## What it does
Sends booking confirmations, receipts and "your bike is due back" reminders by email and push.

## Email sending
```python
import smtplib

def send_email(message):
    server = smtplib.SMTP(SMTP_HOST, 587)
    server.starttls()
    server.login(SMTP_USER, os.environ["SMTP_PASSWORD"])
    server.send_message(message)
    server.quit()
```

## requirements.txt
```
celery
redis
jinja2==3.1.4
firebase-admin>=6
```

## Data
Queue in the shared Redis (replicated). Templates in the repo.

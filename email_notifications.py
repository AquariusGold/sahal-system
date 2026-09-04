"""Best-effort transactional email delivery for SAHAL user notifications."""

import logging
import smtplib
import ssl
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage

from flask import current_app


logger = logging.getLogger(__name__)
_EMAIL_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix='sahal-email')


def send_notification_email(recipient, subject, heading, message, *, action_url=None, action_label='Open SAHAL'):
    """Queue a transactional email without delaying the request that triggered it."""
    if not recipient:
        return False

    config = {
        key: current_app.config.get(key)
        for key in (
            'MAIL_SERVER', 'MAIL_PORT', 'MAIL_USE_TLS', 'MAIL_USE_SSL', 'MAIL_USERNAME',
            'MAIL_PASSWORD', 'MAIL_DEFAULT_SENDER', 'MAIL_FROM_NAME',
        )
    }
    if not config['MAIL_SERVER'] or not config['MAIL_DEFAULT_SENDER']:
        logger.info('Email notification skipped because outbound mail is not configured.')
        return False

    _EMAIL_EXECUTOR.submit(
        _deliver_email, config, recipient, subject, heading, message, action_url, action_label
    )
    return True


def _deliver_email(config, recipient, subject, heading, message, action_url, action_label):
    """Send a queued message over the configured SMTP transport."""
    email = EmailMessage()
    sender_name = config['MAIL_FROM_NAME'] or 'SAHAL Branding Agency'
    email['From'] = f'{sender_name} <{config["MAIL_DEFAULT_SENDER"]}>'
    email['To'] = recipient
    email['Subject'] = subject

    body = f'{heading}\n\n{message}'
    if action_url:
        body += f'\n\n{action_label}: {action_url}'
    body += '\n\nSAHAL Branding Agency'
    email.set_content(body)

    try:
        if config['MAIL_USE_SSL']:
            smtp = smtplib.SMTP_SSL(
                config['MAIL_SERVER'], config['MAIL_PORT'], timeout=15,
                context=ssl.create_default_context(),
            )
        else:
            smtp = smtplib.SMTP(config['MAIL_SERVER'], config['MAIL_PORT'], timeout=15)
        with smtp:
            if config['MAIL_USE_TLS'] and not config['MAIL_USE_SSL']:
                smtp.starttls(context=ssl.create_default_context())
            if config['MAIL_USERNAME']:
                smtp.login(config['MAIL_USERNAME'], config['MAIL_PASSWORD'])
            smtp.send_message(email)
    except (OSError, smtplib.SMTPException):
        logger.exception('Unable to deliver an email notification.')
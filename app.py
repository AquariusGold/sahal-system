"""
SAHAL System - Flask Application
Enterprise web application for talent, products, and business solutions
"""

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_file, g, abort
from functools import wraps
from io import BytesIO
from decimal import Decimal, InvalidOperation
import os
import re
import json
import textwrap
import uuid
import click
import zipfile
import shutil
from urllib.parse import urlparse
from threading import Lock
from datetime import datetime, timedelta
from werkzeug.exceptions import RequestEntityTooLarge
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from dotenv import load_dotenv
load_dotenv()
from config import current_config, init_app as init_config
from email_notifications import send_notification_email
from extensions import db
from sqlalchemy import inspect, text, or_, case, func
from flask_socketio import SocketIO, join_room
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask.cli import with_appcontext
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from PIL import Image, UnidentifiedImageError
from models import (
    User, ROLES, ROLE_LABELS, ServiceCategory, Service, Talent, TALENT_CATEGORIES,
    Order, OrderItem, Quotation, Invoice, Receipt, Conversation, ChatMessage, SahalInquiry, TalentGalleryImage, TalentBookingRequest, specs_to_text, text_to_specs, options_to_text, text_to_options,
)

# Initialize Flask app
app = Flask(__name__)
app.config.from_object(current_config)
init_config(app)
if os.environ.get('FLASK_ENV', 'development') == 'production' and app.config['DEBUG']:
    raise RuntimeError('DEBUG must be disabled in production.')
app.config['WTF_CSRF_CHECK_DEFAULT'] = False
csrf = CSRFProtect(app)
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=['200 per hour'],
    storage_uri=app.config['RATELIMIT_STORAGE_URI'],
)
# The Windows/Werkzeug development server does not provide a reliable Socket.IO
# WebSocket worker. Long polling keeps socket events real-time locally; enable
# WebSocket transport in the production server configuration.
socketio = SocketIO(app, async_mode='threading', transports=['polling'])

ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
IMAGE_FORMATS_BY_EXTENSION = {
    'jpg': {'JPEG'}, 'jpeg': {'JPEG'}, 'png': {'PNG'}, 'gif': {'GIF'}, 'webp': {'WEBP'},
}
PRIVATE_TALENT_REQUEST_UPLOAD_DIR = os.path.join(app.instance_path, 'private_uploads', 'talent_requests')
Image.MAX_IMAGE_PIXELS = 20_000_000


def _uploaded_extension(file_storage):
    """Return a normalized extension without trusting it as file validation."""
    if not file_storage or not file_storage.filename or '.' not in file_storage.filename:
        return ''
    return file_storage.filename.rsplit('.', 1)[-1].lower()


def _save_validated_image(file_storage, upload_dir, relative_path):
    """Verify an image's actual content, then save it with an unguessable filename."""
    ext = _uploaded_extension(file_storage)
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return None
    try:
        file_storage.stream.seek(0)
        with Image.open(file_storage.stream) as image:
            image.verify()
            if image.format not in IMAGE_FORMATS_BY_EXTENSION[ext]:
                return None
        file_storage.stream.seek(0)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        return None

    os.makedirs(upload_dir, exist_ok=True)
    filename = f'{uuid.uuid4().hex}.{ext}'
    file_storage.save(os.path.join(upload_dir, filename))
    return f'{relative_path.rstrip("/")}/{filename}'


def _clean_public_input(value, max_length, *, multiline=False):
    """Reject control characters and oversized public form values before persistence."""
    value = (value or '').strip()
    if len(value) > max_length or '\x00' in value:
        return None
    if any(ord(char) < 32 and char not in ('\n', '\r', '\t') for char in value):
        return None
    if not multiline:
        value = ' '.join(value.split())
    return value


def _valid_email(value):
    return bool(value and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value))


def _valid_website(value):
    if not value:
        return True
    parsed = urlparse(value)
    return parsed.scheme in {'http', 'https'} and bool(parsed.netloc)


def _notify_user(user, subject, heading, message, endpoint=None, **view_args):
    """Queue a transactional notification for a user after a successful state change."""
    if not user or not user.email:
        return False
    action_url = None
    if endpoint:
        action_url = f"{app.config['APP_BASE_URL']}{url_for(endpoint, **view_args)}"
    return send_notification_email(user.email, subject, heading, message, action_url=action_url)


@app.before_request
def enforce_csrf_protection():
    """Protect all application state changes while leaving Engine.IO transport intact."""
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and not request.path.startswith('/socket.io'):
        csrf.protect()


def _load_sahal_projects():
    """Load rendered portfolio project data when the PDF extraction has been run."""
    data_path = os.path.join(app.root_path, 'data', 'sahal_projects.json')
    fallback = [
        {'id': i, 'title': f'Project {i}', 'category': cat, 'image': None, 'summary': 'Portfolio work from the Sahal studio.'}
        for i, cat in enumerate(['Branding', 'Print', 'Signage', 'Packaging', 'Branding', 'Print'], start=1)
    ]
    if not os.path.exists(data_path):
        return fallback
    try:
        with open(data_path, encoding='utf-8') as handle:
            projects = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return fallback
    return projects


def _save_sahal_projects(projects):
    """Persist portfolio records in the same JSON archive used by the public gallery."""
    data_dir = os.path.join(app.root_path, 'data')
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, 'sahal_projects.json'), 'w', encoding='utf-8') as handle:
        json.dump(projects, handle, indent=2, ensure_ascii=False)


def _save_sahal_project_image(file_storage):
    """Save an uploaded project image and return its static-relative archive path."""
    upload_dir = os.path.join(app.root_path, 'static', 'uploads', 'projects')
    return _save_validated_image(file_storage, upload_dir, 'uploads/projects')


def _load_sahal_archive(name, fallback):
    """Load an editable Sahal gallery archive, returning starter content when absent."""
    data_path = os.path.join(app.root_path, 'data', f'sahal_{name}.json')
    if not os.path.exists(data_path):
        return fallback
    try:
        with open(data_path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return fallback


def _save_sahal_archive(name, entries):
    data_dir = os.path.join(app.root_path, 'data')
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, f'sahal_{name}.json'), 'w', encoding='utf-8') as handle:
        json.dump(entries, handle, indent=2, ensure_ascii=False)


def _save_sahal_archive_image(file_storage, archive):
    upload_dir = os.path.join(app.root_path, 'static', 'uploads', archive)
    return _save_validated_image(file_storage, upload_dir, f'uploads/{archive}')


CAROUSEL_PAGES = ('apps', 'catalog', 'talent')
CAROUSEL_DEFAULTS = {
    'slots': [{'id': index, 'image': None} for index in range(1, 10)],
    'pages': {
        'apps': {'start': 1, 'sequence': list(range(1, 10))},
        'catalog': {'start': 4, 'sequence': [4, 5, 6, 7, 8, 9, 1, 2, 3]},
        'talent': {'start': 7, 'sequence': [7, 8, 9, 1, 2, 3, 4, 5, 6]},
    },
}

CAROUSEL_SLIDE_COPY = {
    'apps': [
        ('SAHAL Platform', 'One place for every workflow', 'Move between business tools, client workspaces, and creative services with ease.'),
        ('Connected Work', 'Run the work that matters', 'Manage projects, orders, communication, and operations from one platform.'),
        ('Built for Momentum', 'Make every next step clear', 'Keep teams and clients aligned with practical tools built around daily work.'),
        ('Your Workspace', 'Everything starts here', 'Open the right SAHAL app and continue exactly where your work needs you.'),
        ('Business Operations', 'Stay organized as you grow', 'Bring service delivery, people, and client communication into a focused workspace.'),
        ('Client Experience', 'Clear progress at every stage', 'Give every client a simple way to follow orders, messages, and documents.'),
        ('Creative Services', 'Turn ideas into visible work', 'Explore the tools and talent that bring campaigns, events, and brands to life.'),
        ('Reliable Delivery', 'From request to result', 'Use one connected system to keep the next action visible and accountable.'),
        ('SAHAL', 'Start with the work in front of you', 'Select an app below to manage your next task, project, or client request.'),
    ],
    'catalog': [
        ('Products and Services', 'Print that makes an impression', 'Choose reliable print solutions for campaigns, customer touchpoints, and daily operations.'),
        ('Branding Solutions', 'Build a brand people recognize', 'Bring consistent identity, campaign assets, and business materials together.'),
        ('Event Production', 'Make the occasion feel complete', 'Source visual materials and event essentials that carry the experience.'),
        ('Made to Order', 'Configure the details that matter', 'Choose specifications, quantities, and options around the way your project needs to work.'),
        ('Business Essentials', 'Keep your team presentation-ready', 'Find practical products for sales, service, operations, and client-facing moments.'),
        ('Campaign Ready', 'Give every launch a stronger presence', 'Select assets that make new products, offers, and events easier to notice.'),
        ('Quality in the Details', 'Finish every piece with intent', 'Explore materials and formats built to support your brand at every scale.'),
        ('Simple Ordering', 'From selection to delivery', 'Build your cart, review requirements, and send one clear order to the SAHAL team.'),
        ('SAHAL Catalog', 'Find the right solution for the job', 'Browse our full range of printing, branding, and event services.'),
    ],
    'talent': [
        ('SAHAL Talent', 'Find the right face for the moment', 'Discover professionals available for campaigns, productions, events, and brand work.'),
        ('Talent Agency', 'Bring your brief to life', 'Browse profiles, review portfolios, and send a detailed booking request.'),
        ('Creative Collaboration', 'Talent that fits the brief', 'Connect with people across performance, influence, modelling, music, and dance.'),
        ('Campaign Casting', 'Make the audience stop and look', 'Find creative professionals who can carry the tone and reach of your next campaign.'),
        ('Production Ready', 'From concept to call time', 'Share dates, location, and requirements so the booking team can move quickly.'),
        ('Stories in Motion', 'Find performers built for the frame', 'Explore talent for screen work, stage, social storytelling, and live experiences.'),
        ('A Stronger Lineup', 'People make the experience', 'Choose talent that helps the idea feel credible, memorable, and complete.'),
        ('Booking Support', 'Send a clear request', 'Tell us what you need and receive guidance on availability, logistics, and quote options.'),
        ('SAHAL Talent', 'Discover talent for your next project', 'Browse portfolios and request a booking when you find the right match.'),
    ],
}


def _default_carousel_slide_copy(page, slot_id):
    copy = CAROUSEL_SLIDE_COPY.get(page, CAROUSEL_SLIDE_COPY['apps'])
    eyebrow, title, description = copy[(slot_id - 1) % len(copy)]
    return {'eyebrow': eyebrow, 'title': title, 'description': description}


def _load_hero_carousel():
    data_path = os.path.join(app.root_path, 'data', 'hero_carousel.json')
    if not os.path.exists(data_path):
        data = json.loads(json.dumps(CAROUSEL_DEFAULTS))
    else:
        try:
            with open(data_path, encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            data = json.loads(json.dumps(CAROUSEL_DEFAULTS))
    if not isinstance(data.get('slots'), list) or not isinstance(data.get('pages'), dict):
        data = json.loads(json.dumps(CAROUSEL_DEFAULTS))
    slot_ids = {slot.get('id') for slot in data['slots'] if isinstance(slot, dict) and slot.get('id')}
    for page in CAROUSEL_PAGES:
        config = data['pages'].setdefault(page, json.loads(json.dumps(CAROUSEL_DEFAULTS['pages'][page])))
        saved_copy = config.get('copy') if isinstance(config.get('copy'), dict) else {}
        config['copy'] = {
            str(slot_id): {
                key: str(saved_copy.get(str(slot_id), {}).get(key, default_value))
                for key, default_value in _default_carousel_slide_copy(page, slot_id).items()
            }
            for slot_id in slot_ids
        }
    return data


def _save_hero_carousel(carousel):
    data_dir = os.path.join(app.root_path, 'data')
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, 'hero_carousel.json'), 'w', encoding='utf-8') as handle:
        json.dump(carousel, handle, indent=2)


def _carousel_slides(page):
    carousel = _load_hero_carousel()
    slot_images = {slot.get('id'): slot.get('image') for slot in carousel['slots']}
    config = carousel['pages'].get(page, CAROUSEL_DEFAULTS['pages'][page])
    sequence = [slot_id for slot_id in config.get('sequence', []) if slot_id in slot_images]
    start = config.get('start')
    if start in sequence:
        sequence = sequence[sequence.index(start):] + sequence[:sequence.index(start)]
    return [
        {
            'id': slot_id,
            'image': slot_images[slot_id],
            **_default_carousel_slide_copy(page, slot_id),
            **config.get('copy', {}).get(str(slot_id), {}),
        }
        for slot_id in sequence
    ]


def _save_carousel_image(file_storage):
    upload_dir = os.path.join(app.root_path, 'static', 'uploads', 'carousel')
    return _save_validated_image(file_storage, upload_dir, 'uploads/carousel')


def _sahal_design_defaults():
    return [
        {
            'id': index,
            'title': f'Design {index}',
            'category': category,
            'summary': 'Visual work from the Sahal Branding Agency studio.',
        }
        for index, category in enumerate(
            ['Logo', 'Brochure', 'Poster', 'Business Card', 'Banner', 'Packaging', 'Logo', 'Poster'], start=1
        )
    ]


def _sahal_event_defaults():
    return [
        {'id': 1, 'title': 'Brand Launch Night', 'date': 'Sep 12, 2026', 'location': 'Nairobi, KE'},
        {'id': 2, 'title': 'Design Workshop', 'date': 'Oct 3, 2026', 'location': 'Mombasa, KE'},
        {'id': 3, 'title': 'Client Showcase', 'date': 'Nov 20, 2026', 'location': 'Nairobi, KE'},
    ]


def _build_quotation_pdf(quotation):
    """Create an A4 PDF quotation from its generated order and line items."""
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    page_width, page_height = A4
    margin = 18 * mm
    navy = colors.HexColor('#002766')
    blue = colors.HexColor('#003399')
    yellow = colors.HexColor('#FFCC00')
    muted = colors.HexColor('#6B7280')
    pale = colors.HexColor('#F3F6FC')
    pdf.setFillColor(navy)
    pdf.rect(0, page_height - 58 * mm, page_width, 58 * mm, fill=1, stroke=0)
    logo_path = os.path.join(app.root_path, 'static', 'images', 'Logo White.png')
    if os.path.exists(logo_path):
        pdf.drawImage(logo_path, margin, page_height - 34 * mm, width=44 * mm, height=18 * mm, preserveAspectRatio=True, mask='auto')
    else:
        pdf.setFillColor(yellow)
        pdf.setFont('Helvetica-Bold', 22)
        pdf.drawString(margin, page_height - 27 * mm, 'SAHAL')
    pdf.setFillColor(colors.white)
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawRightString(page_width - margin, page_height - 20 * mm, 'QUOTATION')
    pdf.setFont('Helvetica', 9)
    pdf.drawRightString(page_width - margin, page_height - 27 * mm, quotation.reference)
    y = page_height - 72 * mm
    pdf.setFillColor(navy)
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(margin, y, 'PREPARED FOR')
    pdf.drawString(page_width / 2 + 8 * mm, y, 'QUOTATION DETAILS')
    y -= 6 * mm
    pdf.setFont('Helvetica-Bold', 11)
    pdf.drawString(margin, y, quotation.user.full_name)
    pdf.setFont('Helvetica', 9)
    y -= 5 * mm
    pdf.setFillColor(muted)
    pdf.drawString(margin, y, quotation.user.email)
    if quotation.user.phone_number:
        y -= 5 * mm
        pdf.drawString(margin, y, quotation.user.phone_number)
    detail_x = page_width / 2 + 8 * mm
    detail_y = page_height - 78 * mm
    pdf.setFillColor(colors.HexColor('#111827'))
    pdf.setFont('Helvetica', 9)
    pdf.drawString(detail_x, detail_y, f'Issued: {quotation.created_at.strftime("%d %b %Y")}')
    pdf.drawString(detail_x, detail_y - 5 * mm, f'Related order: #{quotation.order_id}')
    pdf.drawString(detail_x, detail_y - 10 * mm, f'Status: {quotation.status.capitalize()}')
    y = page_height - 112 * mm
    col_x = [margin, margin + 13 * mm, margin + 92 * mm, margin + 116 * mm, page_width - margin]
    pdf.setFillColor(blue)
    pdf.roundRect(margin, y - 8 * mm, page_width - (2 * margin), 10 * mm, 2 * mm, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont('Helvetica-Bold', 8)
    for text, x, alignment in [('NO.', col_x[0] + 3 * mm, 'left'), ('DESCRIPTION', col_x[1], 'left'), ('QTY', col_x[2], 'right'), ('RATE', col_x[3], 'right'), ('AMOUNT', col_x[4], 'right')]:
        if alignment == 'right':
            pdf.drawRightString(x, y - 4 * mm, text)
        else:
            pdf.drawString(x, y - 4 * mm, text)
    y -= 15 * mm
    styles = getSampleStyleSheet()
    description_style = styles['Normal']
    description_style.fontName = 'Helvetica'
    description_style.fontSize = 8.5
    description_style.leading = 11
    description_style.textColor = colors.HexColor('#111827')
    for index, item in enumerate(quotation.order.items, start=1):
        options = ', '.join(f'{key}: {value}' for key, value in item.selected_options_dict.items())
        item_text = f'<b>{item.service.name}</b>' + (f'<br/><font color="#6B7280">{options}</font>' if options else '')
        paragraph = Paragraph(item_text, description_style)
        _, height = paragraph.wrap(72 * mm, 40 * mm)
        row_height = max(13 * mm, height + 6 * mm)
        if y - row_height < 42 * mm:
            pdf.showPage()
            y = page_height - margin
        pdf.setFillColor(pale if index % 2 else colors.white)
        pdf.rect(margin, y - row_height + 3 * mm, page_width - (2 * margin), row_height, fill=1, stroke=0)
        pdf.setFillColor(colors.HexColor('#111827'))
        pdf.setFont('Helvetica', 8.5)
        pdf.drawString(col_x[0] + 3 * mm, y - 4 * mm, f'{index:02d}')
        paragraph.drawOn(pdf, col_x[1], y - height - 1 * mm)
        pdf.drawRightString(col_x[2], y - 4 * mm, str(item.quantity))
        pdf.drawRightString(col_x[3], y - 4 * mm, f'${item.unit_price:,.2f}')
        pdf.setFont('Helvetica-Bold', 8.5)
        pdf.drawRightString(col_x[4], y - 4 * mm, f'${item.subtotal:,.2f}')
        y -= row_height
    y -= 8 * mm
    pdf.setStrokeColor(colors.HexColor('#D1D5DB'))
    pdf.line(margin + 100 * mm, y, page_width - margin, y)
    y -= 8 * mm
    pdf.setFillColor(navy)
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawRightString(page_width - margin - 38 * mm, y, 'TOTAL')
    pdf.setFillColor(blue)
    pdf.setFont('Helvetica-Bold', 17)
    pdf.drawRightString(page_width - margin, y - 2 * mm, f'${quotation.total_price:,.2f}')
    pdf.setFillColor(muted)
    pdf.setFont('Helvetica', 8)
    pdf.drawString(margin, 25 * mm, 'Thank you for choosing SAHAL Branding Agency.')
    pdf.drawRightString(page_width - margin, 25 * mm, f'Generated {quotation.created_at.strftime("%d %b %Y")}')
    pdf.save()
    output.seek(0)
    return output


def _build_invoice_pdf(invoice):
    """Create an A4 invoice PDF for a completed order using the invoice colorway."""
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    page_width, page_height = A4
    margin = 18 * mm
    navy = colors.HexColor('#002766')
    blue = colors.HexColor('#003399')
    muted = colors.HexColor('#6B7280')
    pale = colors.HexColor('#FFFBEB')
    pdf.setFillColor(navy)
    pdf.rect(0, page_height - 58 * mm, page_width, 58 * mm, fill=1, stroke=0)
    logo_path = os.path.join(app.root_path, 'static', 'images', 'Logo White.png')
    if os.path.exists(logo_path):
        pdf.drawImage(logo_path, margin, page_height - 34 * mm, width=44 * mm, height=18 * mm, preserveAspectRatio=True, mask='auto')
    else:
        pdf.setFillColor(colors.white)
        pdf.setFont('Helvetica-Bold', 22)
        pdf.drawString(margin, page_height - 27 * mm, 'SAHAL')
    pdf.setFillColor(navy)
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawRightString(page_width - margin, page_height - 20 * mm, 'INVOICE')
    pdf.setFont('Helvetica', 9)
    pdf.drawRightString(page_width - margin, page_height - 27 * mm, invoice.reference)
    y = page_height - 72 * mm
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(margin, y, 'BILL TO')
    pdf.drawString(page_width / 2 + 8 * mm, y, 'INVOICE DETAILS')
    y -= 6 * mm
    pdf.setFont('Helvetica-Bold', 11)
    pdf.drawString(margin, y, invoice.user.full_name)
    pdf.setFont('Helvetica', 9)
    y -= 5 * mm
    pdf.setFillColor(muted)
    pdf.drawString(margin, y, invoice.user.email)
    if invoice.user.phone_number:
        y -= 5 * mm
        pdf.drawString(margin, y, invoice.user.phone_number)
    detail_x = page_width / 2 + 8 * mm
    detail_y = page_height - 78 * mm
    pdf.setFillColor(colors.HexColor('#111827'))
    pdf.setFont('Helvetica', 9)
    pdf.drawString(detail_x, detail_y, f'Issued: {invoice.created_at.strftime("%d %b %Y")}')
    pdf.drawString(detail_x, detail_y - 5 * mm, f'Completed order: #{invoice.order_id}')
    pdf.drawString(detail_x, detail_y - 10 * mm, f'Status: {invoice.status.capitalize()}')
    y = page_height - 112 * mm
    col_x = [margin, margin + 13 * mm, margin + 92 * mm, margin + 116 * mm, page_width - margin]
    pdf.setFillColor(blue)
    pdf.roundRect(margin, y - 8 * mm, page_width - (2 * margin), 10 * mm, 2 * mm, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont('Helvetica-Bold', 8)
    for text, x, alignment in [('NO.', col_x[0] + 3 * mm, 'left'), ('DESCRIPTION', col_x[1], 'left'), ('QTY', col_x[2], 'right'), ('RATE', col_x[3], 'right'), ('AMOUNT', col_x[4], 'right')]:
        if alignment == 'right':
            pdf.drawRightString(x, y - 4 * mm, text)
        else:
            pdf.drawString(x, y - 4 * mm, text)
    y -= 15 * mm
    for index, item in enumerate(invoice.order.items, start=1):
        options = ', '.join(f'{key}: {value}' for key, value in item.selected_options_dict.items())
        pdf.setFillColor(pale if index % 2 else colors.white)
        pdf.rect(margin, y - 10 * mm, page_width - (2 * margin), 13 * mm, fill=1, stroke=0)
        pdf.setFillColor(colors.HexColor('#111827'))
        pdf.setFont('Helvetica', 8.5)
        pdf.drawString(col_x[0] + 3 * mm, y - 4 * mm, f'{index:02d}')
        pdf.setFont('Helvetica-Bold', 8.5)
        pdf.drawString(col_x[1], y - 4 * mm, item.service.name[:52])
        if options:
            pdf.setFont('Helvetica', 7.5)
            pdf.setFillColor(muted)
            pdf.drawString(col_x[1], y - 8 * mm, options[:76])
        pdf.setFillColor(colors.HexColor('#111827'))
        pdf.setFont('Helvetica', 8.5)
        pdf.drawRightString(col_x[2], y - 4 * mm, str(item.quantity))
        pdf.drawRightString(col_x[3], y - 4 * mm, f'${item.unit_price:,.2f}')
        pdf.setFont('Helvetica-Bold', 8.5)
        pdf.drawRightString(col_x[4], y - 4 * mm, f'${item.subtotal:,.2f}')
        y -= 13 * mm
    y -= 8 * mm
    pdf.setStrokeColor(colors.HexColor('#D1D5DB'))
    pdf.line(margin + 100 * mm, y, page_width - margin, y)
    y -= 8 * mm
    pdf.setFillColor(navy)
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawRightString(page_width - margin - 38 * mm, y, 'AMOUNT DUE')
    pdf.setFillColor(blue)
    pdf.setFont('Helvetica-Bold', 17)
    pdf.drawRightString(page_width - margin, y - 2 * mm, f'${invoice.total_price:,.2f}')
    pdf.setFillColor(muted)
    pdf.setFont('Helvetica', 8)
    pdf.drawString(margin, 25 * mm, 'Thank you for choosing SAHAL Branding Agency.')
    pdf.drawRightString(page_width - margin, 25 * mm, f'Invoice {invoice.reference}')
    pdf.save()
    output.seek(0)
    return output


def _build_receipt_pdf(receipt):
    """Create a grayscale A4 delivery receipt PDF, apart from the Sahal logo."""
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    page_width, page_height = A4
    margin = 18 * mm
    black = colors.HexColor('#111111')
    dark_gray = colors.HexColor('#374151')
    gray = colors.HexColor('#6B7280')
    light_gray = colors.HexColor('#F3F4F6')
    pdf.setFillColor(black)
    pdf.rect(0, page_height - 52 * mm, page_width, 52 * mm, fill=1, stroke=0)
    logo_path = os.path.join(app.root_path, 'static', 'images', 'Logo White.png')
    if os.path.exists(logo_path):
        pdf.drawImage(logo_path, margin, page_height - 32 * mm, width=44 * mm, height=16 * mm, preserveAspectRatio=True, mask='auto')
    pdf.setFillColor(colors.white)
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawRightString(page_width - margin, page_height - 20 * mm, 'DELIVERY RECEIPT')
    pdf.setFont('Helvetica', 9)
    pdf.drawRightString(page_width - margin, page_height - 27 * mm, receipt.reference)
    y = page_height - 70 * mm
    pdf.setFillColor(black)
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(margin, y, 'RECEIVED BY')
    pdf.drawString(page_width / 2 + 8 * mm, y, 'RECEIPT DETAILS')
    y -= 6 * mm
    pdf.setFont('Helvetica-Bold', 11)
    pdf.drawString(margin, y, receipt.user.full_name)
    pdf.setFont('Helvetica', 9)
    y -= 5 * mm
    pdf.setFillColor(gray)
    pdf.drawString(margin, y, receipt.user.email)
    detail_x = page_width / 2 + 8 * mm
    detail_y = page_height - 76 * mm
    pdf.setFillColor(dark_gray)
    pdf.drawString(detail_x, detail_y, f'Delivered: {receipt.created_at.strftime("%d %b %Y")}')
    pdf.drawString(detail_x, detail_y - 5 * mm, f'Order: #{receipt.order_id}')
    pdf.drawString(detail_x, detail_y - 10 * mm, 'Status: Delivered to client')
    y = page_height - 106 * mm
    col_x = [margin, margin + 13 * mm, margin + 92 * mm, margin + 116 * mm, page_width - margin]
    pdf.setFillColor(dark_gray)
    pdf.roundRect(margin, y - 8 * mm, page_width - (2 * margin), 10 * mm, 2 * mm, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont('Helvetica-Bold', 8)
    for text, x, alignment in [('NO.', col_x[0] + 3 * mm, 'left'), ('DESCRIPTION', col_x[1], 'left'), ('QTY', col_x[2], 'right'), ('RATE', col_x[3], 'right'), ('AMOUNT', col_x[4], 'right')]:
        if alignment == 'right':
            pdf.drawRightString(x, y - 4 * mm, text)
        else:
            pdf.drawString(x, y - 4 * mm, text)
    y -= 15 * mm
    for index, item in enumerate(receipt.order.items, start=1):
        pdf.setFillColor(light_gray if index % 2 else colors.white)
        pdf.rect(margin, y - 10 * mm, page_width - (2 * margin), 13 * mm, fill=1, stroke=0)
        pdf.setFillColor(black)
        pdf.setFont('Helvetica', 8.5)
        pdf.drawString(col_x[0] + 3 * mm, y - 4 * mm, f'{index:02d}')
        pdf.setFont('Helvetica-Bold', 8.5)
        pdf.drawString(col_x[1], y - 4 * mm, item.service.name[:52])
        pdf.setFont('Helvetica', 8.5)
        pdf.drawRightString(col_x[2], y - 4 * mm, str(item.quantity))
        pdf.drawRightString(col_x[3], y - 4 * mm, f'${item.unit_price:,.2f}')
        pdf.setFont('Helvetica-Bold', 8.5)
        pdf.drawRightString(col_x[4], y - 4 * mm, f'${item.subtotal:,.2f}')
        y -= 13 * mm
    y -= 8 * mm
    pdf.setStrokeColor(colors.HexColor('#D1D5DB'))
    pdf.line(margin + 100 * mm, y, page_width - margin, y)
    y -= 8 * mm
    pdf.setFillColor(black)
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawRightString(page_width - margin - 38 * mm, y, 'TOTAL RECEIVED')
    pdf.setFont('Helvetica-Bold', 17)
    pdf.drawRightString(page_width - margin, y - 2 * mm, f'${receipt.total_price:,.2f}')
    pdf.setFillColor(gray)
    pdf.setFont('Helvetica', 8)
    pdf.drawString(margin, 25 * mm, 'Thank you for choosing SAHAL Branding Agency.')
    pdf.drawRightString(page_width - margin, 25 * mm, f'Receipt {receipt.reference}')
    pdf.save()
    output.seek(0)
    return output


def _ensure_database_exists():
    """Create the MySQL database if it doesn't exist yet (dev convenience)."""
    uri = app.config['SQLALCHEMY_DATABASE_URI']
    if not uri.startswith('mysql+pymysql://'):
        return
    try:
        import pymysql
        conn = pymysql.connect(
            host=app.config['MYSQL_HOST'],
            port=app.config['MYSQL_PORT'],
            user=app.config['MYSQL_USER'],
            password=app.config['MYSQL_PASSWORD'],
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{app.config['MYSQL_DB']}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[SAHAL] Warning: could not auto-create MySQL database: {exc}")


def _seed_talent_directory():
    """Seed a starter set of talent profiles if the table is empty (dev convenience)."""
    if Talent.query.first():
        return
    tid = 1
    for category in TALENT_CATEGORIES:
        for i in range(1, 6):
            db.session.add(Talent(
                name=f'{category[:-1]} {i}',
                category=category,
                bio=f'Professional {category[:-1].lower()} available for bookings, campaigns, and events.',
                location='Nairobi, KE',
                rating=round(4.5 + (tid % 5) * 0.1, 1),
                reviews=10 + tid * 3,
                followers=1200 + tid * 340,
                bookings=8 + tid,
                photos=(tid % 6) + 4,
                featured=(tid % 4 == 0),
                is_active=True,
            ))
            tid += 1
    db.session.commit()


def _ensure_user_profile_columns():
    """Add profile fields to existing installations that predate the profile page."""
    columns = {column['name'] for column in inspect(db.engine).get_columns('users')}
    additions = {
        'avatar_url': 'VARCHAR(255)',
        'job_title': 'VARCHAR(120)',
        'location': 'VARCHAR(120)',
        'bio': 'TEXT',
        'last_seen_at': 'DATETIME',
        "theme_preference": "VARCHAR(10) NOT NULL DEFAULT 'light'",
        'address': 'VARCHAR(255)',
    }
    for name, definition in additions.items():
        if name not in columns:
            db.session.execute(text(f'ALTER TABLE users ADD COLUMN {name} {definition}'))
    db.session.commit()


def _ensure_chat_indexes():
    """Add query indexes to existing installations without a migration dependency."""
    index_definitions = {
        'conversations': {
            'ix_conversations_updated_at': 'CREATE INDEX ix_conversations_updated_at ON conversations (updated_at)',
        },
        'chat_messages': {
            'ix_chat_messages_conversation_read_sender': 'CREATE INDEX ix_chat_messages_conversation_read_sender ON chat_messages (conversation_id, read_at, sender_id)',
            'ix_chat_messages_conversation_created': 'CREATE INDEX ix_chat_messages_conversation_created ON chat_messages (conversation_id, created_at)',
        },
    }
    inspector = inspect(db.engine)
    for table_name, definitions in index_definitions.items():
        existing = {index['name'] for index in inspector.get_indexes(table_name)}
        for name, statement in definitions.items():
            if name not in existing:
                db.session.execute(text(statement))
    db.session.commit()


if app.config['AUTO_SCHEMA_MANAGEMENT']:
    _ensure_database_exists()
db.init_app(app)
if app.config['AUTO_SCHEMA_MANAGEMENT'] and (not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'):
    with app.app_context():
        db.create_all()
        _ensure_user_profile_columns()
        _ensure_chat_indexes()
        _seed_talent_directory()


@app.cli.command('create-admin')
@click.option('--name', prompt='Full name', help='Administrator full name.')
@click.option('--email', prompt='Email address', help='Administrator email address.')
@click.option('--password', prompt=True, hide_input=True, confirmation_prompt=True, help='Administrator password.')
@with_appcontext
def create_admin(name, email, password):
    """Create one administrator explicitly; no admin is created during startup."""
    email = email.strip().lower()
    if User.query.filter_by(email=email).first():
        raise click.UsageError('An account with that email already exists.')
    if not _valid_new_password(password):
        raise click.UsageError('Password must be at least 8 characters with uppercase, lowercase, and a number.')
    admin = User(full_name=name.strip(), email=email, role='admin', account_type='business', is_active=True)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    click.echo(f'Created administrator {email}.')


@app.cli.command('migrate-private-talent-attachments')
@with_appcontext
def migrate_private_talent_attachments():
    """Move legacy public talent briefs into private storage and update their records."""
    moved = 0
    legacy_dir = os.path.join(app.root_path, 'static', 'uploads', 'talent_requests')
    os.makedirs(PRIVATE_TALENT_REQUEST_UPLOAD_DIR, exist_ok=True)
    for booking_request in TalentBookingRequest.query.filter(TalentBookingRequest.attachment_url.isnot(None)).all():
        old_value = booking_request.attachment_url or ''
        if not old_value.startswith('/static/uploads/talent_requests/'):
            continue
        source_path = os.path.join(legacy_dir, os.path.basename(old_value))
        if not os.path.isfile(source_path):
            continue
        extension = os.path.splitext(source_path)[1].lower().lstrip('.')
        if extension not in ALLOWED_TALENT_REQUEST_EXTENSIONS:
            continue
        filename = f'{uuid.uuid4().hex}.{extension}'
        shutil.move(source_path, os.path.join(PRIVATE_TALENT_REQUEST_UPLOAD_DIR, filename))
        booking_request.attachment_url = filename
        moved += 1
    if moved:
        db.session.commit()
    click.echo(f'Migrated {moved} talent request attachment(s).')

# ===========================
# Authentication Context Processor
# ===========================

@app.context_processor
def inject_user():
    """Make user available in all templates"""
    current_user = _current_session_user()
    user = current_user.to_session_dict() if current_user else {
        'id': None,
        'name': 'Guest',
        'initials': 'G',
        'email': '',
        'role': None,
        'is_authenticated': False
    }
    unread_count = _unread_chat_count(user['id']) if user.get('is_authenticated') else 0
    return dict(user=user, chat_unread_count=unread_count)

# ===========================
# Authentication Decorator
# ===========================

def _auth_failure(status=401):
    if request.path.startswith('/api/') or request.is_json:
        return jsonify({'error': 'Authentication required.'}), status
    if status == 403:
        abort(403)
    return redirect(url_for('login'))


def _current_session_user():
    """Return the live account backing the signed session, or invalidate it."""
    session_data = session.get('user') or {}
    user_id = session_data.get('id')
    if not user_id:
        return None
    user = db.session.get(User, user_id)
    if not user or not user.is_active or session_data.get('role') != user.role:
        session.clear()
        return None
    g.current_user = user
    return user


def login_required(f):
    """Require login for route"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _current_session_user():
            return _auth_failure()
        return f(*args, **kwargs)
    return decorated_function

def role_required(roles):
    """Require specific role(s)"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = getattr(g, 'current_user', None) or _current_session_user()
            if not user:
                return _auth_failure()
            if user.role not in (roles if isinstance(roles, list) else [roles]):
                return _auth_failure(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@app.after_request
def apply_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    if session.get('user', {}).get('is_authenticated'):
        response.headers.setdefault('Cache-Control', 'private, no-store')
    return response


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    if request.path.startswith('/api/') or request.is_json:
        return jsonify({'error': 'Your form session expired. Refresh the page and try again.'}), 400
    flash('Your form session expired. Refresh the page and try again.', 'error')
    return redirect(request.referrer or url_for('index'))


@app.errorhandler(RequestEntityTooLarge)
def handle_upload_too_large(error):
    if request.path.startswith('/api/') or request.is_json:
        return jsonify({'error': 'The uploaded file exceeds the 16 MB limit.'}), 413
    flash('The uploaded file exceeds the 16 MB limit.', 'error')
    return redirect(request.referrer or url_for('index'))

# ===========================
# PUBLIC ROUTES
# ===========================

@app.route('/')
def index():
    """Apps Menu - gateway to all system modules"""
    apps = [
        {
            'name': 'Sahal Branding Agency Portfolio',
            'description': 'Branding & printing services showcase',
            'icon': 'building-2',
            'color': 'from-blue-500 to-blue-700',
            'url': url_for('sahal_home'),
            'badge': None
        },
        {
            'name': 'Products & Services',
            'description': 'Browse the product catalog',
            'icon': 'package',
            'color': 'from-green-500 to-green-700',
            'url': url_for('catalog'),
            'badge': None
        },
        {
            'name': 'Talent Agency',
            'description': 'Discover vetted talent',
            'icon': 'users',
            'color': 'from-purple-500 to-purple-700',
            'url': url_for('talent'),
            'badge': None
        },
        {
            'name': 'Dashboard',
            'description': 'Your business overview',
            'icon': 'layout-dashboard',
            'color': 'from-indigo-500 to-indigo-700',
            'url': url_for('dashboard'),
            'badge': None
        },
        {
            'name': 'Chats & Messaging',
            'description': 'Connect with clients & talent',
            'icon': 'message-circle',
            'color': 'from-teal-500 to-teal-700',
            'url': url_for('chat'),
            'badge': None
        },
        {
            'name': 'Quotations & Invoices',
            'description': 'Manage quotes and billing',
            'icon': 'file-text',
            'color': 'from-orange-500 to-orange-700',
            'url': url_for('quotations'),
            'badge': None
        },
        {
            'name': 'My Profile',
            'description': 'Manage your account details',
            'icon': 'user-circle',
            'color': 'from-pink-500 to-pink-700',
            'url': url_for('my_profile'),
            'badge': None
        },
        {
            'name': 'Settings',
            'description': 'Preferences & configuration',
            'icon': 'settings',
            'color': 'from-slate-500 to-slate-700',
            'url': url_for('settings'),
            'badge': None
        },
    ]
    return render_template('public/index.html', is_dashboard=False, is_apps_menu=True, apps=apps, hero_slides=_carousel_slides('apps'))


def _save_avatar(file_storage):
    """Store a profile avatar and return its static-relative URL."""
    upload_dir = os.path.join(app.root_path, 'static', 'uploads', 'avatars')
    return _save_validated_image(file_storage, upload_dir, 'uploads/avatars')


@app.route('/module/my-profile', methods=['GET', 'POST'])
@login_required
def my_profile():
    """View and update the signed-in user's account profile."""
    profile = User.query.get_or_404(session['user']['id'])

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        if not full_name or not email:
            flash('Your name and email address are required.')
            return redirect(url_for('my_profile'))

        email_owner = User.query.filter(User.email == email, User.id != profile.id).first()
        if email_owner:
            flash('That email address is already linked to another account.')
            return redirect(url_for('my_profile'))

        avatar_file = request.files.get('avatar')
        if avatar_file and avatar_file.filename:
            avatar_url = _save_avatar(avatar_file)
            if not avatar_url:
                flash('Please upload a JPG, PNG, GIF, or WEBP image.')
                return redirect(url_for('my_profile'))
            profile.avatar_url = avatar_url

        profile.full_name = full_name
        profile.email = email
        profile.phone_number = request.form.get('phone_number', '').strip() or None
        profile.account_type = request.form.get('account_type') if request.form.get('account_type') in ('personal', 'business') else profile.account_type
        profile.company_name = request.form.get('company_name', '').strip() or None
        profile.company_website = request.form.get('company_website', '').strip() or None
        profile.industry = request.form.get('industry', '').strip() or None
        profile.job_title = request.form.get('job_title', '').strip() or None
        profile.location = request.form.get('location', '').strip() or None
        profile.address = request.form.get('address', '').strip() or None
        profile.bio = request.form.get('bio', '').strip() or None
        db.session.commit()
        session['user'] = profile.to_session_dict()
        flash('Your profile has been updated.')
        return redirect(url_for('my_profile'))

    return render_template('dashboard/my_profile.html', is_dashboard=True, profile=profile)


def _valid_new_password(password):
    """Require a password that is difficult to guess."""
    return (
        len(password) >= 8
        and bool(re.search(r'[A-Z]', password))
        and bool(re.search(r'[a-z]', password))
        and bool(re.search(r'\d', password))
    )


@app.route('/settings', methods=['GET', 'POST'])
@app.route('/module/settings', methods=['GET', 'POST'])
@login_required
@limiter.limit('10 per hour', methods=['POST'])
def settings():
    """Shared security and appearance settings for the signed-in user."""
    current_user = User.query.get_or_404(session['user']['id'])

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'theme':
            theme = request.form.get('theme')
            if theme not in ('light', 'dark'):
                flash('Choose either light or dark mode.')
            else:
                current_user.theme_preference = theme
                db.session.commit()
                session['user'] = current_user.to_session_dict()
                flash(f'{theme.capitalize()} mode is now your saved preference.')
            return redirect(url_for('settings'))

        if action == 'password':
            current_password = request.form.get('current_password', '')
            new_password = request.form.get('new_password', '')
            confirm_password = request.form.get('confirm_password', '')
            if not current_user.check_password(current_password):
                flash('Your current password is incorrect.')
            elif new_password != confirm_password:
                flash('The new password and confirmation do not match.')
            elif not _valid_new_password(new_password):
                flash('Use at least 8 characters with uppercase, lowercase, and a number.')
            elif current_user.check_password(new_password):
                flash('Choose a new password that is different from your current password.')
            else:
                current_user.set_password(new_password)
                db.session.commit()
                _notify_user(current_user, 'Your SAHAL password was changed', 'Password changed', 'Your password was changed successfully. If you did not make this change, contact SAHAL support immediately.', 'settings')
                flash('Your password has been changed successfully.')
            return redirect(url_for('settings'))

    return render_template('dashboard/settings.html', is_dashboard=True, settings_user=current_user)

@app.route('/module/<module>')
def module_placeholder(module):
    """Placeholder page for modules that are not yet built"""
    module_name = module.replace('-', ' ').title()
    return render_template('public/coming_soon.html', is_dashboard=False, is_apps_menu=True, module_name=module_name)

# ===========================
# TALENT AGENCY (directory + profiles)
# ===========================

@app.route('/talent')
def talent():
    """Talent Agency directory"""
    category = request.args.get('category', 'all')
    query = Talent.query.filter_by(is_active=True)
    if category != 'all':
        query = query.filter_by(category=category)
    talents = query.order_by(Talent.featured.desc(), Talent.name).all()
    return render_template(
        'talent/directory.html',
        is_dashboard=False,
        is_talent_site=True,
        categories=TALENT_CATEGORIES,
        active_category=category,
        talents=talents,
        hero_slides=_carousel_slides('talent')
    )


TALENT_REQUEST_FIELDS = {
    'Actors': (
        ('project_type', 'Project Type'), ('role_type', 'Role Type'),
        ('character_description', 'Character Description'), ('filming_dates', 'Filming Dates'),
        ('travel_requirements', 'Travel Requirements'), ('union_status', 'Union Status'),
    ),
    'Influencers': (
        ('campaign_type', 'Campaign Type'), ('deliverables', 'Deliverables'),
        ('usage_rights', 'Usage Rights Duration'), ('brand_handle', 'Brand Handle / Product Link'),
        ('campaign_goals', 'Campaign Goals'), ('key_messages', 'Key Messages / Hashtags'),
    ),
    'Models': (
        ('assignment_type', 'Assignment Type'), ('fitting_dates', 'Fitting Dates'),
        ('hair_makeup_provided', 'Hair / Makeup Provided'), ('wardrobe_requirements', 'Wardrobe Requirements'),
        ('usage_rights', 'Usage Rights'),
    ),
    'Musicians': (
        ('performance_type', 'Performance Type'), ('set_duration', 'Set Duration'),
        ('sound_system_provided', 'Sound System / PA Provided'), ('backline_requirements', 'Backline Equipment Requirements'),
    ),
    'Dancers': (
        ('performance_type', 'Performance Type'), ('dance_style', 'Dance Style Required'),
        ('rehearsal_schedule', 'Rehearsal Schedule and Dates'), ('costume_requirements', 'Costume / Attire Requirements'),
    ),
}
TALENT_REQUEST_STATUSES = ('new', 'in_review', 'approved', 'declined', 'completed')
ALLOWED_TALENT_REQUEST_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt'}


def _save_talent_request_attachment(file_storage):
    """Content-check and store a private talent brief outside the static directory."""
    ext = _uploaded_extension(file_storage)
    if ext not in ALLOWED_TALENT_REQUEST_EXTENSIONS:
        return None
    try:
        file_storage.stream.seek(0)
        signature = file_storage.stream.read(8192)
        file_storage.stream.seek(0)
        if ext == 'pdf' and not signature.startswith(b'%PDF-'):
            return None
        if ext == 'doc' and not signature.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
            return None
        if ext == 'docx':
            if not zipfile.is_zipfile(file_storage.stream):
                return None
            file_storage.stream.seek(0)
            with zipfile.ZipFile(file_storage.stream) as archive:
                names = set(archive.namelist())
                if '[Content_Types].xml' not in names or not any(name.startswith('word/') for name in names):
                    return None
        if ext == 'txt':
            signature.decode('utf-8')
        file_storage.stream.seek(0)
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile):
        return None

    os.makedirs(PRIVATE_TALENT_REQUEST_UPLOAD_DIR, exist_ok=True)
    filename = f'{uuid.uuid4().hex}.{ext}'
    file_storage.save(os.path.join(PRIVATE_TALENT_REQUEST_UPLOAD_DIR, filename))
    return filename


def _build_talent_request_pdf(booking_request):
    """Create a concise export of a talent booking request for internal use."""
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    page_width, page_height = A4
    margin = 18 * mm
    y = page_height - margin

    pdf.setFillColor(colors.HexColor('#0B1F3A'))
    pdf.setFont('Helvetica-Bold', 20)
    pdf.drawString(margin, y, 'SAHAL Talent Request')
    y -= 9 * mm
    pdf.setFillColor(colors.HexColor('#2563EB'))
    pdf.setFont('Helvetica-Bold', 11)
    pdf.drawString(margin, y, f'Request #{booking_request.id}  |  {booking_request.status_label}')
    y -= 10 * mm

    def add_field(label, value):
        nonlocal y
        if not value:
            return
        if y < 30 * mm:
            pdf.showPage()
            y = page_height - margin
        pdf.setFillColor(colors.HexColor('#6B7280'))
        pdf.setFont('Helvetica-Bold', 8)
        pdf.drawString(margin, y, label.upper())
        y -= 4 * mm
        pdf.setFillColor(colors.HexColor('#1F2937'))
        pdf.setFont('Helvetica', 9)
        for line in textwrap.wrap(str(value), width=105) or ['']:
            pdf.drawString(margin, y, line)
            y -= 4.5 * mm
        y -= 2 * mm

    add_field('Talent', f'{booking_request.talent.name} ({booking_request.category})')
    add_field('Client', f'{booking_request.full_name} | {booking_request.company_name or "No company"}')
    add_field('Contact', f'{booking_request.email} | {booking_request.phone_number or "No phone supplied"}')
    add_field('Project', booking_request.project_name)
    add_field('Budget', booking_request.budget_range)
    dates = ' to '.join(value.strftime('%d %b %Y') for value in (booking_request.event_start_date, booking_request.event_end_date) if value)
    add_field('Dates and duration', f'{dates or "Not supplied"} | {booking_request.duration or "Duration not supplied"}')
    add_field('Location', ' | '.join(value for value in (booking_request.location_mode, booking_request.city, booking_request.venue_details) if value))
    for label, value in booking_request.category_details_dict.items():
        add_field(label, value)
    pdf.save()
    output.seek(0)
    return output


@app.route('/talent/<int:talent_id>', methods=['GET', 'POST'])
@limiter.limit('5 per hour', methods=['POST'])
def talent_profile(talent_id):
    """Talent profile with a persisted booking and quote request flow."""
    profile = Talent.query.filter_by(id=talent_id, is_active=True).first()
    if not profile:
        return redirect(url_for('talent'))

    if request.method == 'POST':
        full_name = _clean_public_input(request.form.get('full_name'), 120)
        email = _clean_public_input(request.form.get('email'), 120)
        email = email.lower() if email else None
        project_name = _clean_public_input(request.form.get('project_name'), 180)
        budget_range = _clean_public_input(request.form.get('budget_range'), 100)
        if not all((full_name, email, project_name, budget_range)) or not _valid_email(email):
            flash('Complete the required client and project details before sending your request.', 'error')
            return redirect(url_for('talent_profile', talent_id=profile.id))

        def parse_date(value):
            try:
                return datetime.strptime(value, '%Y-%m-%d').date() if value else None
            except ValueError:
                return None

        start_date = parse_date(request.form.get('event_start_date', ''))
        end_date = parse_date(request.form.get('event_end_date', ''))
        if request.form.get('event_start_date') and not start_date or request.form.get('event_end_date') and not end_date:
            flash('Use valid project dates.', 'error')
            return redirect(url_for('talent_profile', talent_id=profile.id))
        if start_date and end_date and end_date < start_date:
            flash('The project end date cannot be before the start date.', 'error')
            return redirect(url_for('talent_profile', talent_id=profile.id))

        category_details = {}
        for field, label in TALENT_REQUEST_FIELDS.get(profile.category, ()):
            value = _clean_public_input(request.form.get(field), 2000, multiline=True)
            if value is None:
                flash('One of the project requirement fields is too long or contains invalid characters.', 'error')
                return redirect(url_for('talent_profile', talent_id=profile.id))
            if value:
                category_details[label] = value

        optional_fields = {
            'company_name': (120, False), 'phone_number': (40, False), 'duration': (100, False),
            'city': (120, False), 'venue_details': (255, True),
        }
        cleaned_optional = {
            field: _clean_public_input(request.form.get(field), max_length, multiline=multiline)
            for field, (max_length, multiline) in optional_fields.items()
        }
        if any(value is None for value in cleaned_optional.values()):
            flash('One or more booking details are invalid or too long.', 'error')
            return redirect(url_for('talent_profile', talent_id=profile.id))
        location_mode = _clean_public_input(request.form.get('location_mode'), 30)
        if location_mode not in ('On-site', 'Remote', 'Virtual', ''):
            flash('Choose a valid location mode.', 'error')
            return redirect(url_for('talent_profile', talent_id=profile.id))
        attachment = _save_talent_request_attachment(request.files.get('script_excerpt'))
        if request.files.get('script_excerpt') and request.files.get('script_excerpt').filename and not attachment:
            flash('Script excerpts must be PDF, DOC, DOCX, or TXT files.', 'error')
            return redirect(url_for('talent_profile', talent_id=profile.id))

        user_id = session.get('user', {}).get('id') if session.get('user', {}).get('is_authenticated') else None
        booking_request = TalentBookingRequest(
            talent_id=profile.id,
            client_user_id=user_id,
            full_name=full_name,
            company_name=cleaned_optional['company_name'],
            email=email,
            phone_number=cleaned_optional['phone_number'],
            project_name=project_name,
            budget_range=budget_range,
            event_start_date=start_date,
            event_end_date=end_date,
            duration=cleaned_optional['duration'],
            city=cleaned_optional['city'],
            venue_details=cleaned_optional['venue_details'],
            location_mode=location_mode,
            category=profile.category,
            category_details=json.dumps(category_details),
            attachment_url=attachment,
        )
        db.session.add(booking_request)
        profile.bookings += 1
        db.session.commit()
        flash(f'Your request for {profile.name} has been sent. Our team will be in touch shortly.', 'success')
        return redirect(url_for('talent_profile', talent_id=profile.id))

    return render_template(
        'talent/profile.html',
        is_dashboard=False,
        is_talent_site=True,
        talent=profile,
    )


@app.route('/talent-requests/<int:request_id>/attachment')
@login_required
def talent_request_attachment_download(request_id):
    """Serve a private booking attachment only to its client, assignee, or an admin."""
    booking_request = TalentBookingRequest.query.get_or_404(request_id)
    current_user = _current_session_user()
    if not current_user or (
        current_user.role != 'admin'
        and current_user.id not in {booking_request.client_user_id, booking_request.assigned_staff_id}
    ):
        return _auth_failure(403)
    filename = os.path.basename(booking_request.attachment_url or '')
    if not filename or filename != booking_request.attachment_url:
        abort(404)
    attachment_path = os.path.join(PRIVATE_TALENT_REQUEST_UPLOAD_DIR, filename)
    if not os.path.isfile(attachment_path):
        abort(404)
    return send_file(attachment_path, as_attachment=True, download_name=f'talent-request-{request_id}-{filename}')

# ===========================
# PRODUCTS & SERVICES (e-commerce shop)
# ===========================

@app.route('/catalog')
def catalog():
    """Products & Services shop - browse by category"""
    category = request.args.get('category', 'all')
    db_categories = ServiceCategory.query.filter_by(is_active=True).order_by(ServiceCategory.order).all()
    categories = [{'slug': 'all', 'name': 'All Products', 'icon': 'layout-grid'}] + [
        {'slug': c.slug, 'name': c.name, 'icon': c.icon or 'package'} for c in db_categories
    ]

    query = Service.query.join(ServiceCategory).filter(Service.is_active == True)
    if category != 'all':
        query = query.filter(ServiceCategory.slug == category)
    products = query.order_by(ServiceCategory.order, Service.order).all()

    return render_template(
        'shop/catalog.html',
        is_dashboard=False,
        is_shop_site=True,
        categories=categories,
        active_category=category,
        products=products,
        hero_slides=_carousel_slides('catalog')
    )

@app.route('/catalog/cart')
def catalog_cart():
    """Shopping cart page (cart contents rendered client-side from localStorage)"""
    return render_template('shop/cart.html', is_dashboard=False, is_shop_site=True)

@app.route('/catalog/<int:product_id>')
def product_detail(product_id):
    """Product/service details and configuration before adding to the cart."""
    product = Service.query.filter_by(id=product_id, is_active=True).first()
    if not product:
        return redirect(url_for('catalog'))
    return render_template('shop/product_detail.html', is_dashboard=False, is_shop_site=True, product=product)


@app.route('/api/catalog/orders', methods=['POST'])
@limiter.limit('3 per hour')
def api_catalog_checkout():
    """Validate local-cart items and convert them into one persisted order."""
    current_user = _current_session_user()
    if not current_user:
        return jsonify({'error': 'Please sign in before placing your order.', 'login_url': url_for('login')}), 401

    payload = request.get_json(silent=True) or {}
    cart_items = payload.get('items')
    if not isinstance(cart_items, list) or not cart_items:
        return jsonify({'error': 'Your cart is empty.'}), 400

    prepared_items = []
    total = Decimal('0')
    for item in cart_items:
        if not isinstance(item, dict):
            return jsonify({'error': 'Your cart contains an invalid item.'}), 400
        product_id = item.get('product_id')
        quantity = item.get('qty')
        if not isinstance(product_id, int) or not isinstance(quantity, int) or quantity < 1 or quantity > 999:
            return jsonify({'error': 'Each item must have a valid quantity.'}), 400
        product = Service.query.filter_by(id=product_id, is_active=True).first()
        if not product:
            return jsonify({'error': 'One of the items is no longer available. Remove it and try again.'}), 400

        selected_options = item.get('selected_options') or {}
        if not isinstance(selected_options, dict):
            return jsonify({'error': f'Invalid options selected for {product.name}.'}), 400
        allowed_options = {option.get('name'): set(option.get('values', [])) for option in product.options_list}
        if any(name not in allowed_options or value not in allowed_options[name] for name, value in selected_options.items()):
            return jsonify({'error': f'An option selected for {product.name} is no longer available.'}), 400

        unit_price = Decimal(product.base_price or 0)
        subtotal = unit_price * quantity
        prepared_items.append((product, quantity, unit_price, subtotal, selected_options))
        total += subtotal

    order = Order(user_id=current_user.id, status='placed', total_price=total)
    db.session.add(order)
    db.session.flush()
    for product, quantity, unit_price, subtotal, selected_options in prepared_items:
        db.session.add(OrderItem(
            order_id=order.id,
            service_id=product.id,
            quantity=quantity,
            unit_price=unit_price,
            subtotal=subtotal,
            selected_options=json.dumps(selected_options),
        ))
    quotation = Quotation(
        order_id=order.id,
        user_id=current_user.id,
        reference=f"QT-{datetime.now().strftime('%Y%m%d')}-{order.id:05d}",
        total_price=total,
    )
    db.session.add(quotation)
    db.session.commit()
    _notify_user(current_user, f'Order #{order.id} placed', 'Your order has been placed', 'We received your order and will update you when a staff member is assigned.', 'client_order_detail', order_id=order.id)
    _notify_user(current_user, f'Quotation {quotation.reference} is ready', 'Your quotation is ready', 'A quotation has been created for your new order.', 'quotation_detail', quotation_id=quotation.id)
    return jsonify({'success': True, 'order_id': order.id})


# ===========================
# SAHAL BRANDING AGENCY PORTFOLIO (sub-site)
# ===========================

@app.route('/sahal')
def sahal_home():
    """Sahal Branding Agency Portfolio - Home"""
    featured_ids = (14, 37, 33)
    projects_by_id = {project.get('id'): project for project in _load_sahal_projects()}
    featured_projects = [projects_by_id[project_id] for project_id in featured_ids if project_id in projects_by_id]
    return render_template('sahal/home.html', is_dashboard=False, is_sahal_site=True, featured_projects=featured_projects)

@app.route('/sahal/projects')
def sahal_projects():
    """Sahal Branding Agency Portfolio - Projects"""
    projects = _load_sahal_projects()
    return render_template('sahal/projects.html', is_dashboard=False, is_sahal_site=True, projects=projects)

@app.route('/sahal/designs')
def sahal_designs():
    """Sahal Branding Agency Portfolio - Designs"""
    designs = _load_sahal_archive('designs', _sahal_design_defaults())
    return render_template('sahal/designs.html', is_dashboard=False, is_sahal_site=True, designs=designs)

@app.route('/sahal/events')
def sahal_events():
    """Sahal Branding Agency Portfolio - Events"""
    events = _load_sahal_archive('events', _sahal_event_defaults())
    return render_template('sahal/events.html', is_dashboard=False, is_sahal_site=True, events=events)

@app.route('/sahal/about')
def sahal_about():
    """Sahal Branding Agency Portfolio - About"""
    return render_template('sahal/about.html', is_dashboard=False, is_sahal_site=True)

@app.route('/sahal/contact', methods=['GET', 'POST'])
@limiter.limit('5 per hour', methods=['POST'])
def sahal_contact():
    """Capture detailed project enquiries for the Sahal Branding Agency."""
    if request.method == 'POST':
        full_name = _clean_public_input(request.form.get('full_name'), 120)
        email = _clean_public_input(request.form.get('email'), 120)
        email = email.lower() if email else None
        services = [_clean_public_input(service, 80) for service in request.form.getlist('services')[:10]]
        project_details = _clean_public_input(request.form.get('project_details'), 5000, multiline=True)
        if not full_name or not _valid_email(email) or not services or any(service is None for service in services) or not project_details:
            flash('Please complete your name, email, service interests, and project details.')
            return render_template('sahal/contact.html', is_dashboard=False, is_sahal_site=True, submitted=False, form_data=request.form)

        optional_fields = {
            'phone_number': (40, False), 'company_name': (120, False), 'company_website': (255, False),
            'project_title': (180, False), 'project_goal': (1500, True), 'target_audience': (1000, True),
            'deliverables': (1500, True), 'timeline': (120, False), 'budget_range': (100, False),
            'contact_preference': (50, False),
        }
        cleaned = {
            field: _clean_public_input(request.form.get(field), max_length, multiline=multiline)
            for field, (max_length, multiline) in optional_fields.items()
        }
        if any(value is None for value in cleaned.values()) or not _valid_website(cleaned['company_website']):
            flash('One or more details are invalid or too long. Use a full http:// or https:// website address.')
            return render_template('sahal/contact.html', is_dashboard=False, is_sahal_site=True, submitted=False, form_data=request.form)

        db.session.add(SahalInquiry(
            full_name=full_name,
            email=email,
            phone_number=cleaned['phone_number'] or None,
            company_name=cleaned['company_name'] or None,
            company_website=cleaned['company_website'] or None,
            services=', '.join(services),
            project_title=cleaned['project_title'] or None,
            project_goal=cleaned['project_goal'] or None,
            target_audience=cleaned['target_audience'] or None,
            deliverables=cleaned['deliverables'] or None,
            timeline=cleaned['timeline'] or None,
            budget_range=cleaned['budget_range'] or None,
            contact_preference=cleaned['contact_preference'] or None,
            project_details=project_details,
        ))
        db.session.commit()
        return redirect(url_for('sahal_contact', submitted='1'))
    return render_template('sahal/contact.html', is_dashboard=False, is_sahal_site=True, submitted=request.args.get('submitted') == '1', form_data=request.form)

# ===========================
# AUTHENTICATION ROUTES
# ===========================

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit('5 per minute', methods=['POST'])
def login():
    """User login"""
    error = None
    if request.method == 'POST':
        email = _clean_public_input(request.form.get('email'), 120)
        email = email.lower() if email else ''
        password = request.form.get('password', '')

        user = User.query.filter_by(email=email).first() if email else None
        if user and user.is_active and user.check_password(password):
            session['user'] = user.to_session_dict()
            return redirect(url_for('index'))
        error = 'Invalid email or password.'

    return render_template('auth/login.html', is_dashboard=False, error=error)

@app.route('/signup', methods=['GET', 'POST'])
@limiter.limit('3 per hour', methods=['POST'])
def signup():
    """User registration - all self-registered accounts are Normal User (client) role"""
    error = None
    if request.method == 'POST':
        name = _clean_public_input(
            f"{request.form.get('first_name', '').strip()} {request.form.get('last_name', '').strip()}", 120
        )
        email = _clean_public_input(request.form.get('email'), 120)
        email = email.lower() if email else ''
        phone_number = _clean_public_input(request.form.get('phone_number'), 40)
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not name or not _valid_email(email) or not phone_number or not password:
            error = 'Please fill in all required fields.'
        elif password != confirm_password:
            error = 'Passwords do not match.'
        elif not _valid_new_password(password):
            error = 'Use at least 8 characters with uppercase, lowercase, and a number.'
        elif User.query.filter_by(email=email).first():
            error = 'An account with that email already exists.'
        else:
            user = User(full_name=name, email=email, phone_number=phone_number, role='client', account_type='personal', is_active=True)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            session['user'] = user.to_session_dict()
            _notify_user(user, 'Welcome to SAHAL Branding Agency', 'Welcome to SAHAL', 'Your account is ready. You can now browse services, place orders, and follow their progress from your dashboard.', 'dashboard')
            return redirect(url_for('index'))

    return render_template('auth/signup.html', is_dashboard=False, error=error)

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    """User logout"""
    session.clear()
    return redirect(url_for('index'))

# ===========================
# DASHBOARD ROUTES
# ===========================

@app.route('/dashboard')
@login_required
def dashboard():
    """Main dashboard - redirect based on role"""
    user_role = session.get('user', {}).get('role')
    
    if user_role == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif user_role == 'staff':
        return redirect(url_for('staff_dashboard'))
    else:
        return redirect(url_for('user_dashboard'))

@app.route('/dashboard/admin')
@login_required
@role_required('admin')
def admin_dashboard():
    """Admin dashboard"""
    completed_delivery_statuses = ('fulfilled', 'dispatched', 'received')
    delivered_revenue = db.session.query(db.func.coalesce(db.func.sum(Order.total_price), 0)).filter(
        Order.status.in_(['dispatched', 'received'])
    ).scalar()
    stats = {
        'total_revenue': delivered_revenue,
        'active_orders': Order.query.filter(~Order.status.in_(['fulfilled', 'dispatched', 'received', 'cancelled'])).count(),
        'talent_roster': Talent.query.count(),
        'pending_assignments': Order.query.filter_by(status='placed').count(),
    }

    activity_feed = []
    activity_feed.extend({'timestamp': order.created_at, 'icon': 'shopping-bag', 'tone': 'blue', 'title': 'Order placed', 'description': f'{order.user.full_name} placed order #{order.id}'} for order in Order.query.order_by(Order.created_at.desc()).limit(5))
    activity_feed.extend({'timestamp': quotation.created_at, 'icon': 'file-text', 'tone': 'blue', 'title': 'Quotation generated', 'description': f'{quotation.reference} created for {quotation.user.full_name}'} for quotation in Quotation.query.order_by(Quotation.created_at.desc()).limit(5))
    activity_feed.extend({'timestamp': invoice.created_at, 'icon': 'receipt', 'tone': 'blue', 'title': 'Invoice generated', 'description': f'{invoice.reference} created for order #{invoice.order_id}'} for invoice in Invoice.query.order_by(Invoice.created_at.desc()).limit(5))
    activity_feed.extend({'timestamp': receipt.created_at, 'icon': 'badge-check', 'tone': 'gray', 'title': 'Receipt generated', 'description': f'{receipt.reference} created for order #{receipt.order_id}'} for receipt in Receipt.query.order_by(Receipt.created_at.desc()).limit(5))
    activity_feed.extend({'timestamp': order.assigned_at, 'icon': 'user-check', 'tone': 'blue', 'title': 'Order assigned', 'description': f'Order #{order.id} assigned to {order.assigned_staff.full_name}'} for order in Order.query.filter(Order.assigned_at.isnot(None)).order_by(Order.assigned_at.desc()).limit(5))
    activity_feed.extend({'timestamp': order.fulfilled_at, 'icon': 'circle-check', 'tone': 'green', 'title': 'Order completed', 'description': f'Order #{order.id} marked completed'} for order in Order.query.filter(Order.fulfilled_at.isnot(None)).order_by(Order.fulfilled_at.desc()).limit(5))
    activity_feed.extend({'timestamp': order.dispatched_at, 'icon': 'truck', 'tone': 'gray', 'title': 'Order delivered', 'description': f'Order #{order.id} marked delivered to client'} for order in Order.query.filter(Order.dispatched_at.isnot(None)).order_by(Order.dispatched_at.desc()).limit(5))
    activity_feed.extend({'timestamp': user.created_at, 'icon': 'user-plus', 'tone': 'blue', 'title': 'New user registered', 'description': f'{user.full_name} created a {user.role} account'} for user in User.query.order_by(User.created_at.desc()).limit(5))
    activity_feed = sorted((item for item in activity_feed if item['timestamp']), key=lambda item: item['timestamp'], reverse=True)[:5]
    
    staff_performers = (
        db.session.query(
            User,
            func.coalesce(
                func.sum(case((Order.status.in_(completed_delivery_statuses), 1), else_=0)),
                0,
            ).label('completed_orders'),
        )
        .outerjoin(Order, Order.assigned_staff_id == User.id)
        .filter(User.role == 'staff', User.is_active.is_(True))
        .group_by(User.id)
        .order_by(db.desc('completed_orders'), User.full_name.asc())
        .all()
    )

    return render_template(
        'dashboard/admin.html',
        is_dashboard=True,
        stats=stats,
        activity=activity_feed,
        staff_performers=staff_performers,
    )

@app.route('/dashboard/user')
@login_required
@role_required('client')
def user_dashboard():
    """Normal User dashboard"""
    orders = Order.query.filter_by(user_id=session['user']['id']).order_by(Order.created_at.desc()).all()
    stats = {
        'active_orders': sum(order.status not in ('received', 'cancelled') for order in orders),
        'completed_orders': sum(order.status == 'received' for order in orders),
        'unread_messages': _unread_chat_count(session['user']['id']),
    }

    return render_template('dashboard/client.html', is_dashboard=True, stats=stats, orders=orders)

@app.route('/dashboard/staff')
@login_required
@role_required('staff')
def staff_dashboard():
    """Staff dashboard"""
    jobs_query = Order.query.filter(
        Order.assigned_staff_id == session['user']['id'],
        Order.status != 'cancelled',
    ).order_by(Order.created_at.desc())
    all_jobs = jobs_query.all()
    per_page = request.args.get('per_page', 20, type=int)
    if per_page not in (20, 50, 100):
        per_page = 20
    page = max(1, request.args.get('page', 1, type=int))
    pagination = jobs_query.paginate(page=page, per_page=per_page, error_out=False)
    stats = {
        'completed_jobs': sum(job.status in ('fulfilled', 'dispatched', 'received') for job in all_jobs),
        'open_jobs': sum(job.status in ('assigned', 'accepted') for job in all_jobs),
        'total_assigned_value': sum(float(job.total_price) for job in all_jobs),
        'rating': 0,
    }

    return render_template('dashboard/worker.html', is_dashboard=True, stats=stats, jobs=pagination.items, pagination=pagination, per_page=per_page)

# ===========================
# ADMIN: USER MANAGEMENT PORTAL
# ===========================

@app.route('/admin/users')
@login_required
@role_required('admin')
def admin_users():
    """List all users and allow role assignment"""
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('dashboard/admin_users.html', is_dashboard=True, users=users, roles=ROLES, role_labels=ROLE_LABELS)

@app.route('/admin/users/<int:user_id>/role', methods=['POST'])
@login_required
@role_required('admin')
def admin_update_user_role(user_id):
    """Assign a role (admin/staff/user) to a user account"""
    new_role = request.form.get('role')
    if new_role in ROLES:
        target = User.query.get(user_id)
        if target:
            target.role = new_role
            db.session.commit()
            # Keep the active session in sync if the admin updated their own role
            if session.get('user', {}).get('id') == target.id:
                session['user'] = target.to_session_dict()
    return redirect(url_for('admin_users'))

# ===========================
# ADMIN: PRODUCTS & SERVICES CATALOG MANAGEMENT
# ===========================

def _slugify(value):
    value = re.sub(r'[^\w\s-]', '', value).strip().lower()
    return re.sub(r'[\s_-]+', '-', value)

def _unique_slug(model, base_slug, exclude_id=None):
    slug = base_slug or 'item'
    counter = 2
    while True:
        query = model.query.filter_by(slug=slug)
        if exclude_id:
            query = query.filter(model.id != exclude_id)
        if not query.first():
            return slug
        slug = f"{base_slug}-{counter}"
        counter += 1

def _save_product_image(file_storage, subfolder='products'):
    """Save an uploaded image and return its public URL, or None."""
    upload_dir = os.path.join(os.path.dirname(app.config['UPLOAD_FOLDER']), subfolder)
    return _save_validated_image(file_storage, upload_dir, f'/static/uploads/{subfolder}')


def _save_talent_gallery_images(files, image_urls=''):
    """Save uploaded or linked gallery images and return their public URLs."""
    urls = []
    for file_storage in files or []:
        image_url = _save_product_image(file_storage, subfolder='talent/gallery')
        if image_url:
            urls.append(image_url)
    for image_url in image_urls.splitlines():
        image_url = image_url.strip()
        if image_url:
            urls.append(image_url)
    return urls

@app.route('/admin/catalog')
@login_required
@role_required('admin')
def admin_catalog():
    """View & manage the Products & Services catalog"""
    categories = ServiceCategory.query.order_by(ServiceCategory.order).all()
    return render_template('dashboard/admin_catalog.html', is_dashboard=True, categories=categories)

@app.route('/admin/catalog/categories/new', methods=['POST'])
@login_required
@role_required('admin')
def admin_catalog_category_new():
    """Add a new catalog category"""
    name = request.form.get('name', '').strip()
    icon = request.form.get('icon', '').strip() or 'package'
    if name:
        max_order = db.session.query(db.func.max(ServiceCategory.order)).scalar() or 0
        category = ServiceCategory(
            name=name,
            slug=_unique_slug(ServiceCategory, _slugify(name)),
            icon=icon,
            is_active=True,
            order=max_order + 1,
        )
        db.session.add(category)
        db.session.commit()
    return redirect(url_for('admin_catalog'))

@app.route('/admin/catalog/categories/<int:category_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_catalog_category_delete(category_id):
    """Delete a category and its products"""
    category = ServiceCategory.query.get_or_404(category_id)
    db.session.delete(category)
    db.session.commit()
    return redirect(url_for('admin_catalog'))

@app.route('/admin/catalog/categories/reorder', methods=['POST'])
@login_required
@role_required('admin')
def admin_catalog_category_reorder():
    """Persist the drag-and-drop category display order (AJAX, expects JSON list of ids)"""
    data = request.get_json(silent=True) or {}
    ordered_ids = data.get('order', [])
    for index, category_id in enumerate(ordered_ids):
        ServiceCategory.query.filter_by(id=category_id).update({'order': index})
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/catalog/products/new', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_catalog_product_new():
    """Add a new product/service entry"""
    categories = ServiceCategory.query.order_by(ServiceCategory.order).all()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        category_id = request.form.get('category_id', type=int)
        description = request.form.get('description', '').strip()
        price_raw = request.form.get('base_price', '').strip()
        pricing_type = request.form.get('pricing_type', 'fixed')
        image_url = _save_product_image(request.files.get('image')) or request.form.get('image_url', '').strip() or None

        if name and category_id:
            max_order = db.session.query(db.func.max(Service.order)).filter_by(category_id=category_id).scalar() or 0
            product = Service(
                name=name,
                category_id=category_id,
                description=description,
                base_price=price_raw or None,
                pricing_type=pricing_type,
                image_url=image_url,
                slug=_unique_slug(Service, _slugify(name)),
                order=max_order + 1,
                is_active=True,
                long_description=request.form.get('long_description', '').strip() or None,
                specifications=json.dumps(text_to_specs(request.form.get('specifications', ''))),
                options=json.dumps(text_to_options(request.form.get('options', ''))),
            )
            db.session.add(product)
            db.session.commit()
            return redirect(url_for('admin_catalog'))

    return render_template(
        'dashboard/admin_catalog_form.html', is_dashboard=True, categories=categories, product=None,
        selected_category_id=request.values.get('category_id', type=int),
    )

@app.route('/admin/catalog/products/<int:product_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_catalog_product_edit(product_id):
    """Edit an existing product/service entry"""
    product = Service.query.get_or_404(product_id)
    categories = ServiceCategory.query.order_by(ServiceCategory.order).all()

    if request.method == 'POST':
        product.name = request.form.get('name', '').strip() or product.name
        product.category_id = request.form.get('category_id', type=int) or product.category_id
        product.description = request.form.get('description', '').strip()
        price_raw = request.form.get('base_price', '').strip()
        product.base_price = price_raw or None
        product.pricing_type = request.form.get('pricing_type', 'fixed')
        product.is_active = request.form.get('is_active') == 'on'
        product.long_description = request.form.get('long_description', '').strip() or None
        product.specifications = json.dumps(text_to_specs(request.form.get('specifications', '')))
        product.options = json.dumps(text_to_options(request.form.get('options', '')))

        new_image = _save_product_image(request.files.get('image'))
        if new_image:
            product.image_url = new_image
        elif request.form.get('image_url', '').strip():
            product.image_url = request.form.get('image_url').strip()

        db.session.commit()
        return redirect(url_for('admin_catalog'))

    return render_template(
        'dashboard/admin_catalog_form.html', is_dashboard=True, categories=categories, product=product,
        specifications_text=specs_to_text(product.specifications_list), selected_category_id=product.category_id,
        options_text=options_to_text(product.options_list),
    )

@app.route('/admin/catalog/products/<int:product_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_catalog_product_delete(product_id):
    """Delete a product/service entry"""
    product = Service.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    return redirect(url_for('admin_catalog'))

# ===========================
# ADMIN: TALENT AGENCY MANAGEMENT
# ===========================

@app.route('/admin/talent')
@login_required
@role_required('admin')
def admin_talent():
    """View all talent profiles"""
    category = request.args.get('category', 'all')
    query = Talent.query
    if category != 'all':
        query = query.filter_by(category=category)
    talents = query.order_by(Talent.category, Talent.name).all()
    return render_template(
        'dashboard/admin_talent.html',
        is_dashboard=True,
        talents=talents,
        categories=TALENT_CATEGORIES,
        active_category=category,
    )

@app.route('/admin/talent/new', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_talent_new():
    """Create a new talent profile"""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name:
            talent_obj = Talent(
                name=name,
                category=request.form.get('category', 'Actors'),
                bio=request.form.get('bio', '').strip(),
                location=request.form.get('location', '').strip(),
                photo_url=_save_product_image(request.files.get('photo'), subfolder='talent')
                    or request.form.get('photo_url', '').strip() or None,
                rating=request.form.get('rating', type=float) or 4.5,
                reviews=request.form.get('reviews', type=int) or 0,
                followers=request.form.get('followers', type=int) or 0,
                bookings=request.form.get('bookings', type=int) or 0,
                featured=request.form.get('featured') == 'on',
                is_active=True,
            )
            db.session.add(talent_obj)
            db.session.flush()
            for image_url in _save_talent_gallery_images(
                request.files.getlist('gallery_images'), request.form.get('gallery_image_urls', '')
            ):
                db.session.add(TalentGalleryImage(
                    talent_id=talent_obj.id,
                    image_url=image_url,
                    order=len(talent_obj.gallery_images),
                ))
            db.session.commit()
            return redirect(url_for('admin_talent'))

    return render_template('dashboard/admin_talent_form.html', is_dashboard=True, categories=TALENT_CATEGORIES, talent=None)

@app.route('/admin/talent/<int:talent_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_talent_edit(talent_id):
    """Edit an existing talent profile"""
    talent_obj = Talent.query.get_or_404(talent_id)

    if request.method == 'POST':
        talent_obj.name = request.form.get('name', '').strip() or talent_obj.name
        talent_obj.category = request.form.get('category', talent_obj.category)
        talent_obj.bio = request.form.get('bio', '').strip()
        talent_obj.location = request.form.get('location', '').strip()
        talent_obj.rating = request.form.get('rating', type=float) or talent_obj.rating
        talent_obj.reviews = request.form.get('reviews', type=int) or 0
        talent_obj.followers = request.form.get('followers', type=int) or 0
        talent_obj.bookings = request.form.get('bookings', type=int) or 0
        talent_obj.featured = request.form.get('featured') == 'on'
        talent_obj.is_active = request.form.get('is_active') == 'on'

        new_photo = _save_product_image(request.files.get('photo'), subfolder='talent')
        if new_photo:
            talent_obj.photo_url = new_photo
        elif request.form.get('photo_url', '').strip():
            talent_obj.photo_url = request.form.get('photo_url').strip()

        for image_url in _save_talent_gallery_images(
            request.files.getlist('gallery_images'), request.form.get('gallery_image_urls', '')
        ):
            max_order = db.session.query(db.func.max(TalentGalleryImage.order)).filter_by(talent_id=talent_obj.id).scalar()
            db.session.add(TalentGalleryImage(
                talent_id=talent_obj.id,
                image_url=image_url,
                order=(max_order or 0) + 1,
            ))
        db.session.commit()
        return redirect(url_for('admin_talent'))

    return render_template('dashboard/admin_talent_form.html', is_dashboard=True, categories=TALENT_CATEGORIES, talent=talent_obj)

@app.route('/admin/talent/<int:talent_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_talent_delete(talent_id):
    """Delete a talent profile"""
    talent_obj = Talent.query.get_or_404(talent_id)
    db.session.delete(talent_obj)
    db.session.commit()
    return redirect(url_for('admin_talent'))


@app.route('/admin/talent/<int:talent_id>/gallery/<int:image_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_talent_gallery_image_delete(talent_id, image_id):
    """Remove one image from a talent profile gallery."""
    image = TalentGalleryImage.query.filter_by(id=image_id, talent_id=talent_id).first_or_404()
    db.session.delete(image)
    db.session.commit()
    return redirect(url_for('admin_talent_edit', talent_id=talent_id))


@app.route('/admin/talent-requests')
@login_required
@role_required('admin')
def admin_talent_requests():
    """Review and manage booking requests submitted through talent profiles."""
    selected_status = request.args.get('status', '')
    selected_category = request.args.get('category', '')
    query = TalentBookingRequest.query
    if selected_status in TALENT_REQUEST_STATUSES:
        query = query.filter_by(status=selected_status)
    if selected_category in TALENT_CATEGORIES:
        query = query.filter_by(category=selected_category)
    return render_template(
        'dashboard/admin_talent_requests.html',
        is_dashboard=True,
        talent_requests=query.order_by(TalentBookingRequest.created_at.desc()).all(),
        staff_members=User.query.filter_by(role='staff', is_active=True).order_by(User.full_name).all(),
        categories=TALENT_CATEGORIES,
        statuses=TALENT_REQUEST_STATUSES,
        selected_status=selected_status,
        selected_category=selected_category,
    )


@app.route('/admin/talent-requests/<int:request_id>/update', methods=['POST'])
@login_required
@role_required('admin')
def admin_talent_request_update(request_id):
    """Update the operational status and staff owner for a talent request."""
    booking_request = TalentBookingRequest.query.get_or_404(request_id)
    status = request.form.get('status', '')
    staff_id = request.form.get('assigned_staff_id', type=int)
    if status in TALENT_REQUEST_STATUSES:
        booking_request.status = status
    if staff_id:
        staff_member = User.query.filter_by(id=staff_id, role='staff', is_active=True).first()
        if staff_member:
            booking_request.assigned_staff_id = staff_member.id
    elif request.form.get('assigned_staff_id') == '':
        booking_request.assigned_staff_id = None
    db.session.commit()
    flash(f'Talent request #{booking_request.id} updated.', 'success')
    return redirect(url_for('admin_talent_requests'))


@app.route('/admin/talent-requests/<int:request_id>/export')
@login_required
@role_required('admin')
def admin_talent_request_export(request_id):
    """Download a structured PDF copy of a booking request."""
    booking_request = TalentBookingRequest.query.get_or_404(request_id)
    return send_file(
        _build_talent_request_pdf(booking_request),
        as_attachment=True,
        download_name=f'talent-request-{booking_request.id}.pdf',
        mimetype='application/pdf',
    )


@app.route('/admin/carousel', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_carousel():
    """Manage the shared public hero carousel image pool and page sequences."""
    carousel = _load_hero_carousel()
    if request.method == 'POST':
        for slot in carousel['slots']:
            slot_id = slot['id']
            if request.form.get(f'remove_slot_{slot_id}') == 'on':
                slot['image'] = None
            new_image = _save_carousel_image(request.files.get(f'slot_{slot_id}'))
            if new_image:
                slot['image'] = new_image
        valid_ids = {slot['id'] for slot in carousel['slots']}
        for page in CAROUSEL_PAGES:
            raw_sequence = request.form.get(f'{page}_sequence', '')
            sequence = []
            for value in raw_sequence.split(','):
                try:
                    slot_id = int(value.strip())
                except ValueError:
                    continue
                if slot_id in valid_ids and slot_id not in sequence:
                    sequence.append(slot_id)
            slide_copy = {}
            for slot_id in valid_ids:
                defaults = _default_carousel_slide_copy(page, slot_id)
                slide_copy[str(slot_id)] = {
                    'eyebrow': request.form.get(f'{page}_{slot_id}_eyebrow', '').strip() or defaults['eyebrow'],
                    'title': request.form.get(f'{page}_{slot_id}_title', '').strip() or defaults['title'],
                    'description': request.form.get(f'{page}_{slot_id}_description', '').strip() or defaults['description'],
                }
            carousel['pages'][page] = {
                'start': request.form.get(f'{page}_start', type=int) or sequence[0] if sequence else 1,
                'sequence': sequence or list(range(1, 10)),
                'copy': slide_copy,
            }
        _save_hero_carousel(carousel)
        flash('Hero carousel configuration saved.', 'success')
        return redirect(url_for('admin_carousel'))
    return render_template('dashboard/admin_carousel.html', is_dashboard=True, carousel=carousel, pages=CAROUSEL_PAGES, copy_defaults=CAROUSEL_SLIDE_COPY)

# ===========================
# ADMIN: SAHAL PROJECTS MANAGEMENT
# ===========================

@app.route('/admin/projects')
@login_required
@role_required('admin')
def admin_projects():
    """Manage the Sahal Branding Agency portfolio gallery."""
    return render_template('dashboard/admin_projects.html', is_dashboard=True, projects=_load_sahal_projects())


@app.route('/admin/projects/new', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_project_new():
    """Add a project to the public Sahal portfolio."""
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if title:
            projects = _load_sahal_projects()
            image = _save_sahal_project_image(request.files.get('image'))
            next_id = max((project.get('id', 0) for project in projects), default=0) + 1
            projects.append({
                'id': next_id,
                'title': title,
                'category': request.form.get('category', '').strip() or 'Brand Identity',
                'summary': request.form.get('summary', '').strip() or 'Portfolio work from the Sahal studio.',
                'image': image,
            })
            _save_sahal_projects(projects)
            flash('Project added to the Sahal portfolio.', 'success')
            return redirect(url_for('admin_projects'))
        flash('A project title is required.', 'error')
    return render_template('dashboard/admin_project_form.html', is_dashboard=True, project=None)


@app.route('/admin/projects/<int:project_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_project_edit(project_id):
    """Edit a project in the public Sahal portfolio."""
    projects = _load_sahal_projects()
    project = next((item for item in projects if item.get('id') == project_id), None)
    if project is None:
        flash('Project not found.', 'error')
        return redirect(url_for('admin_projects'))
    if request.method == 'POST':
        project['title'] = request.form.get('title', '').strip() or project['title']
        project['category'] = request.form.get('category', '').strip() or 'Brand Identity'
        project['summary'] = request.form.get('summary', '').strip() or 'Portfolio work from the Sahal studio.'
        new_image = _save_sahal_project_image(request.files.get('image'))
        if new_image:
            project['image'] = new_image
        _save_sahal_projects(projects)
        flash('Project updated.', 'success')
        return redirect(url_for('admin_projects'))
    return render_template('dashboard/admin_project_form.html', is_dashboard=True, project=project)


@app.route('/admin/projects/<int:project_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_project_delete(project_id):
    """Remove a project record from the public Sahal portfolio."""
    projects = _load_sahal_projects()
    remaining_projects = [project for project in projects if project.get('id') != project_id]
    if len(remaining_projects) == len(projects):
        flash('Project not found.', 'error')
    else:
        _save_sahal_projects(remaining_projects)
        flash('Project removed from the Sahal portfolio.', 'success')
    return redirect(url_for('admin_projects'))


def _archive_entry(entries, entry_id):
    return next((entry for entry in entries if entry.get('id') == entry_id), None)


@app.route('/admin/designs')
@login_required
@role_required('admin')
def admin_designs():
    return render_template('dashboard/admin_designs.html', is_dashboard=True, designs=_load_sahal_archive('designs', _sahal_design_defaults()))


@app.route('/admin/designs/new', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_design_new():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if title:
            designs = _load_sahal_archive('designs', _sahal_design_defaults())
            designs.append({'id': max((item.get('id', 0) for item in designs), default=0) + 1, 'title': title, 'category': request.form.get('category', '').strip() or 'Design', 'summary': request.form.get('summary', '').strip() or 'Visual work from the Sahal Branding Agency studio.', 'image': _save_sahal_archive_image(request.files.get('image'), 'designs')})
            _save_sahal_archive('designs', designs)
            return redirect(url_for('admin_designs'))
    return render_template('dashboard/admin_design_form.html', is_dashboard=True, design=None)


@app.route('/admin/designs/<int:design_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_design_edit(design_id):
    designs = _load_sahal_archive('designs', _sahal_design_defaults())
    design = _archive_entry(designs, design_id)
    if design is None:
        return redirect(url_for('admin_designs'))
    if request.method == 'POST':
        design['title'] = request.form.get('title', '').strip() or design['title']
        design['category'] = request.form.get('category', '').strip() or 'Design'
        design['summary'] = request.form.get('summary', '').strip() or 'Visual work from the Sahal Branding Agency studio.'
        image = _save_sahal_archive_image(request.files.get('image'), 'designs')
        if image:
            design['image'] = image
        _save_sahal_archive('designs', designs)
        return redirect(url_for('admin_designs'))
    return render_template('dashboard/admin_design_form.html', is_dashboard=True, design=design)


@app.route('/admin/designs/<int:design_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_design_delete(design_id):
    designs = _load_sahal_archive('designs', _sahal_design_defaults())
    _save_sahal_archive('designs', [item for item in designs if item.get('id') != design_id])
    return redirect(url_for('admin_designs'))


@app.route('/admin/events')
@login_required
@role_required('admin')
def admin_events():
    return render_template('dashboard/admin_events.html', is_dashboard=True, events=_load_sahal_archive('events', _sahal_event_defaults()))


@app.route('/admin/events/new', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_event_new():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if title:
            events = _load_sahal_archive('events', _sahal_event_defaults())
            events.append({'id': max((item.get('id', 0) for item in events), default=0) + 1, 'title': title, 'date': request.form.get('date', '').strip(), 'location': request.form.get('location', '').strip(), 'summary': request.form.get('summary', '').strip() or 'A Sahal Branding Agency event.', 'image': _save_sahal_archive_image(request.files.get('image'), 'events')})
            _save_sahal_archive('events', events)
            return redirect(url_for('admin_events'))
    return render_template('dashboard/admin_event_form.html', is_dashboard=True, event=None)


@app.route('/admin/events/<int:event_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_event_edit(event_id):
    events = _load_sahal_archive('events', _sahal_event_defaults())
    event = _archive_entry(events, event_id)
    if event is None:
        return redirect(url_for('admin_events'))
    if request.method == 'POST':
        event['title'] = request.form.get('title', '').strip() or event['title']
        event['date'] = request.form.get('date', '').strip()
        event['location'] = request.form.get('location', '').strip()
        event['summary'] = request.form.get('summary', '').strip() or 'A Sahal Branding Agency event.'
        image = _save_sahal_archive_image(request.files.get('image'), 'events')
        if image:
            event['image'] = image
        _save_sahal_archive('events', events)
        return redirect(url_for('admin_events'))
    return render_template('dashboard/admin_event_form.html', is_dashboard=True, event=event)


@app.route('/admin/events/<int:event_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_event_delete(event_id):
    events = _load_sahal_archive('events', _sahal_event_defaults())
    _save_sahal_archive('events', [item for item in events if item.get('id') != event_id])
    return redirect(url_for('admin_events'))

# ===========================
# ADMIN: ORDERS MANAGEMENT
# ===========================


@app.route('/admin/orders')
@login_required
@role_required('admin')
def admin_orders():
    """List all orders for admin management"""
    search = request.args.get('search', '').strip()
    status = request.args.get('status', '').strip()
    order_date = request.args.get('date', '').strip()
    per_page = request.args.get('per_page', 50, type=int)
    if per_page not in (50, 100, 200):
        per_page = 50
    page = max(1, request.args.get('page', 1, type=int))
    query = Order.query.outerjoin(User, Order.user_id == User.id).outerjoin(OrderItem, Order.id == OrderItem.order_id).outerjoin(Service, OrderItem.service_id == Service.id)
    if search:
        pattern = f'%{search}%'
        search_filters = [
            User.full_name.ilike(pattern), User.email.ilike(pattern), User.phone_number.ilike(pattern),
            User.company_name.ilike(pattern), Service.name.ilike(pattern),
        ]
        if search.isdigit():
            search_filters.append(Order.id == int(search))
        query = query.filter(or_(*search_filters))
    if status in Order.STAGE_LABELS:
        query = query.filter(Order.status == status)
    if order_date:
        try:
            selected_date = datetime.strptime(order_date, '%Y-%m-%d').date()
            query = query.filter(db.func.date(Order.created_at) == selected_date)
        except ValueError:
            pass
    pagination = query.distinct().order_by(Order.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return render_template(
        'dashboard/admin_orders.html', is_dashboard=True, orders=pagination.items, pagination=pagination,
        per_page=per_page, search=search, selected_status=status, order_date=order_date,
    )


@app.route('/admin/orders/<int:order_id>')
@login_required
@role_required('admin')
def admin_order_detail(order_id):
    """View a single order and its items"""
    order = Order.query.get_or_404(order_id)
    staff_members = User.query.filter_by(role='staff', is_active=True).order_by(User.full_name).all()
    return render_template(
        'dashboard/admin_order_detail.html',
        is_dashboard=True,
        order=order,
        staff_members=staff_members,
        client_address=_order_client_address(order),
    )


@app.route('/admin/orders/<int:order_id>/assign', methods=['POST'])
@login_required
@role_required('admin')
def admin_assign_order(order_id):
    """Assign a newly placed order to an active staff member."""
    order = Order.query.get_or_404(order_id)
    staff_id = request.form.get('staff_id', type=int)
    staff_member = User.query.filter_by(id=staff_id, role='staff', is_active=True).first()
    if staff_member and order.status == 'placed':
        order.assigned_staff_id = staff_member.id
        order.status = 'assigned'
        order.assigned_at = datetime.utcnow()
        db.session.commit()
        _notify_user(order.user, f'Order #{order.id} has been assigned', 'Your order is now in progress', f'{staff_member.full_name} has been assigned to your order.', 'client_order_detail', order_id=order.id)
        _notify_user(staff_member, f'New assigned order #{order.id}', 'You have a new assigned order', 'Please review the order details and accept the job when you are ready to begin.', 'staff_job_detail', order_id=order.id)
    return redirect(url_for('admin_order_detail', order_id=order.id))


@app.route('/admin/orders/<int:order_id>/dispatch', methods=['POST'])
@login_required
@role_required('admin')
def admin_dispatch_order(order_id):
    """Mark a fulfilled order as dispatched or delivered."""
    order = Order.query.get_or_404(order_id)
    if order.status == 'fulfilled':
        order.status = 'dispatched'
        order.dispatched_at = datetime.utcnow()
        receipt = None
        if not order.receipt:
            receipt = Receipt(order_id=order.id, user_id=order.user_id, reference=f"RCT-{datetime.now().strftime('%Y%m%d')}-{order.id:05d}", total_price=order.total_price)
            db.session.add(receipt)
        db.session.commit()
        _notify_user(order.user, f'Order #{order.id} has been delivered', 'Your order has been delivered', 'Your order has been marked as delivered. Thank you for choosing SAHAL.', 'client_order_detail', order_id=order.id)
        if receipt:
            _notify_user(order.user, f'Receipt {receipt.reference} is ready', 'Your receipt is ready', 'A receipt has been created for your delivered order.', 'receipt_detail', receipt_id=receipt.id)
    return redirect(url_for('admin_order_detail', order_id=order.id))


@app.route('/admin/orders/<int:order_id>/cancel', methods=['POST'])
@login_required
@role_required('admin')
def admin_cancel_order(order_id):
    """Cancel an order while preserving its cancelled audit record."""
    order = Order.query.get_or_404(order_id)
    if order.status == 'cancelled':
        flash(f'Order #{order.id} is already cancelled.', 'info')
        return redirect(url_for('admin_order_detail', order_id=order.id))

    # Generated financial documents no longer apply once an order is cancelled.
    for document in (order.quotation, order.invoice, order.receipt):
        if document:
            db.session.delete(document)

    order.status = 'cancelled'
    order.assigned_staff_id = None
    db.session.commit()
    flash(f'Order #{order.id} has been cancelled and removed from active work and revenue.', 'success')
    return redirect(url_for('admin_order_detail', order_id=order.id))


@app.route('/jobs')
@login_required
@role_required('staff')
def staff_jobs():
    """List orders assigned to the current staff member."""
    jobs = Order.query.filter(
        Order.assigned_staff_id == session['user']['id'],
        Order.status != 'cancelled',
    ).order_by(Order.created_at.desc()).all()
    return render_template('dashboard/staff_jobs.html', is_dashboard=True, jobs=jobs)


@app.route('/jobs/<int:order_id>')
@login_required
@role_required('staff')
def staff_job_detail(order_id):
    """Show an assigned order and its client requirements to the assigned staff member."""
    order = Order.query.filter(
        Order.id == order_id,
        Order.assigned_staff_id == session['user']['id'],
        Order.status != 'cancelled',
    ).first_or_404()
    return render_template('dashboard/staff_job_detail.html', is_dashboard=True, order=order, client_address=_order_client_address(order))


@app.route('/jobs/<int:order_id>/update', methods=['POST'])
@login_required
@role_required('staff')
def update_job_status(order_id):
    """Allow staff to accept an assigned job or mark an accepted job fulfilled."""
    order = Order.query.filter(
        Order.id == order_id,
        Order.assigned_staff_id == session['user']['id'],
        Order.status != 'cancelled',
    ).first_or_404()
    action = request.form.get('action')
    invoice = None
    if action == 'accept' and order.status == 'assigned':
        order.status = 'accepted'
        order.accepted_at = datetime.utcnow()
    elif action == 'fulfill' and order.status == 'accepted':
        order.status = 'fulfilled'
        order.fulfilled_at = datetime.utcnow()
        if not order.invoice:
            invoice = Invoice(order_id=order.id, user_id=order.user_id, reference=f"INV-{datetime.now().strftime('%Y%m%d')}-{order.id:05d}", total_price=order.total_price)
            db.session.add(invoice)
    else:
        return redirect(url_for('staff_job_detail', order_id=order.id))
    db.session.commit()
    if action == 'accept':
        _notify_user(order.user, f'Order #{order.id} has been accepted', 'Your order has been accepted', 'Your assigned SAHAL specialist has accepted the work and is now preparing your order.', 'client_order_detail', order_id=order.id)
    elif action == 'fulfill':
        _notify_user(order.user, f'Order #{order.id} has been completed', 'Your order is complete', 'Our team has completed your order. It will be marked as delivered when dispatch is confirmed.', 'client_order_detail', order_id=order.id)
        if invoice:
            _notify_user(order.user, f'Invoice {invoice.reference} is ready', 'Your invoice is ready', 'An invoice has been created for your completed order.', 'invoice_detail', invoice_id=invoice.id)
    return redirect(url_for('staff_job_detail', order_id=order.id))


@app.route('/my-orders')
@login_required
@role_required('client')
def client_orders():
    """List the current client's orders with their lifecycle progress."""
    orders = Order.query.filter_by(user_id=session['user']['id']).order_by(Order.created_at.desc()).all()
    return render_template('dashboard/client_orders.html', is_dashboard=True, orders=orders)


@app.route('/my-orders/<int:order_id>')
@login_required
@role_required('client')
def client_order_detail(order_id):
    """Show the current client's order details and progress timeline."""
    order = Order.query.filter_by(id=order_id, user_id=session['user']['id']).first_or_404()
    return render_template('dashboard/client_order_detail.html', is_dashboard=True, order=order)


@app.route('/my-orders/<int:order_id>/receive', methods=['POST'])
@login_required
@role_required('client')
def client_receive_order(order_id):
    """Confirm receipt of an order after the admin has dispatched it."""
    order = Order.query.filter_by(id=order_id, user_id=session['user']['id']).first_or_404()
    if order.status == 'dispatched':
        order.status = 'received'
        order.received_at = datetime.utcnow()
        db.session.commit()
        _notify_user(order.user, f'Order #{order.id} delivery confirmed', 'Delivery confirmed', 'We recorded your delivery confirmation. Thank you for choosing SAHAL.', 'client_order_detail', order_id=order.id)
        if order.assigned_staff:
            _notify_user(order.assigned_staff, f'Order #{order.id} delivery confirmed', 'Client delivery confirmation', 'The client confirmed receipt of the completed order.', 'staff_job_detail', order_id=order.id)
    return redirect(url_for('client_order_detail', order_id=order.id))

# ===========================
# DASHBOARD FEATURE ROUTES
# ===========================

CHAT_ACTIVE_ORDER_STATUSES = ('placed', 'assigned', 'accepted')
CHAT_CONNECTED_USERS = {}
CHAT_PRESENCE_LOCK = Lock()
CHAT_PRESENCE_TOUCH_INTERVAL = timedelta(minutes=1)


def _touch_chat_presence(user):
    now = datetime.utcnow()
    if user.last_seen_at and now - user.last_seen_at < CHAT_PRESENCE_TOUCH_INTERVAL:
        return
    user.last_seen_at = now
    db.session.commit()


def _is_chat_online(user):
    with CHAT_PRESENCE_LOCK:
        return CHAT_CONNECTED_USERS.get(user.id, 0) > 0


def _conversation_for_users(first_user_id, second_user_id, create=False):
    participant_one_id, participant_two_id = sorted((first_user_id, second_user_id))
    conversation = Conversation.query.filter_by(
        participant_one_id=participant_one_id,
        participant_two_id=participant_two_id,
    ).first()
    if conversation is None and create:
        conversation = Conversation(
            participant_one_id=participant_one_id,
            participant_two_id=participant_two_id,
        )
        db.session.add(conversation)
        db.session.flush()
    return conversation


def _chat_contacts_for(user):
    """Return the role- and order-scoped people this user is allowed to message."""
    contacts = {}

    def add_contact(contact, label=None, detail=None):
        if contact and contact.id != user.id and contact.is_active:
            contacts[contact.id] = {
                'user': contact,
                'label': label or contact.full_name,
                'detail': detail or contact.role_label,
            }

    active_orders = Order.query.filter(Order.status.in_(CHAT_ACTIVE_ORDER_STATUSES))
    primary_admin = User.query.filter_by(role='admin', is_active=True).order_by(User.id).first()

    if user.role == 'client':
        # The primary administrator receives customer-service messages. If more than
        # one admin exists, the next one is additionally exposed as the admin contact.
        add_contact(primary_admin, 'Customer Service', 'SAHAL support')
        customer_orders = active_orders.filter_by(user_id=user.id).all()
        if customer_orders:
            admin_contact = User.query.filter(
                User.role == 'admin', User.is_active == True, User.id != (primary_admin.id if primary_admin else 0)
            ).order_by(User.id).first()
            add_contact(admin_contact, 'Admin', 'Account administration')
            staff_ids = {order.assigned_staff_id for order in customer_orders if order.assigned_staff_id}
            for staff_member in User.query.filter(User.id.in_(staff_ids), User.role == 'staff', User.is_active == True).order_by(User.full_name):
                add_contact(staff_member)

    elif user.role == 'staff':
        add_contact(primary_admin, 'Admin', 'SAHAL administration')
        client_ids = {
            order.user_id for order in active_orders.filter_by(assigned_staff_id=user.id).all()
        }
        for client in User.query.filter(User.id.in_(client_ids), User.role == 'client', User.is_active == True).order_by(User.full_name):
            add_contact(client)

    elif user.role == 'admin':
        for staff_member in User.query.filter_by(role='staff', is_active=True).order_by(User.full_name):
            add_contact(staff_member)
        client_ids = {order.user_id for order in active_orders.all()}
        for client in User.query.filter(User.id.in_(client_ids), User.role == 'client', User.is_active == True).order_by(User.full_name):
            add_contact(client)

    return contacts


def _unread_chat_count(user_id):
    return ChatMessage.query.filter(ChatMessage.sender_id != user_id, ChatMessage.read_at.is_(None)).join(
        Conversation, ChatMessage.conversation_id == Conversation.id
    ).filter(
        (Conversation.participant_one_id == user_id) | (Conversation.participant_two_id == user_id)
    ).count()


def _chat_message_payload(message, viewer_id):
    return {
        'id': message.id,
        'body': message.body,
        'sent_at': message.created_at.isoformat(),
        'is_mine': message.sender_id == viewer_id,
        'sender_name': message.sender.full_name,
        'sender_avatar_url': url_for('static', filename=message.sender.avatar_url) if message.sender.avatar_url else None,
        'sender_initials': message.sender.initials,
    }


def _chat_contact_payload(contact, viewer_id):
    person = contact['user']
    conversation = _conversation_for_users(viewer_id, person.id)
    last_message = conversation.messages[-1] if conversation and conversation.messages else None
    unread_count = 0
    if conversation:
        unread_count = ChatMessage.query.filter_by(conversation_id=conversation.id, read_at=None).filter(
            ChatMessage.sender_id != viewer_id
        ).count()
    return {
        'id': person.id,
        'name': contact['label'],
        'detail': contact['detail'],
        'avatar_url': url_for('static', filename=person.avatar_url) if person.avatar_url else None,
        'initials': person.initials,
        'online': _is_chat_online(person),
        'unread_count': unread_count,
        'last_message': last_message.body if last_message else 'No messages yet',
        'last_message_at': last_message.created_at.isoformat() if last_message else None,
    }


def _permitted_chat_contact(user, contact_id):
    return _chat_contacts_for(user).get(contact_id)


def _chat_user_room(user_id):
    return f'chat-user-{user_id}'


def _emit_chat_unread_count(user_id):
    socketio.emit('chat:unread-count', {'total_unread': _unread_chat_count(user_id)}, to=_chat_user_room(user_id))


def _emit_chat_contacts_changed(user_ids):
    for user_id in set(user_ids):
        if user_id:
            socketio.emit('chat:contacts-changed', {}, to=_chat_user_room(user_id))


def _chat_presence_audience(user):
    """Find users whose contact list can show this user's online status."""
    audience = {user.id}
    for candidate in User.query.filter(User.id != user.id, User.is_active == True).all():
        if user.id in _chat_contacts_for(candidate):
            audience.add(candidate.id)
    return audience


def _emit_chat_presence(user):
    payload = {'user_id': user.id, 'online': _is_chat_online(user)}
    for user_id in _chat_presence_audience(user):
        socketio.emit('chat:presence', payload, to=_chat_user_room(user_id))


def _mark_chat_conversation_read(user, contact_id):
    conversation = _conversation_for_users(user.id, contact_id)
    if not conversation:
        return
    updated = ChatMessage.query.filter(
        ChatMessage.conversation_id == conversation.id,
        ChatMessage.sender_id != user.id,
        ChatMessage.read_at.is_(None),
    ).update({'read_at': datetime.utcnow()}, synchronize_session=False)
    if updated:
        db.session.commit()
        _emit_chat_unread_count(user.id)
        _emit_chat_contacts_changed((user.id, contact_id))


def _create_chat_message(sender, contact_id, body):
    contact = _permitted_chat_contact(sender, contact_id)
    if not contact:
        return None, None
    conversation = _conversation_for_users(sender.id, contact_id, create=True)
    message = ChatMessage(conversation_id=conversation.id, sender_id=sender.id, body=body)
    sender.last_seen_at = datetime.utcnow()
    conversation.updated_at = datetime.utcnow()
    db.session.add(message)
    db.session.commit()
    return message, contact['user']


def _publish_chat_message(message, sender, recipient):
    socketio.emit(
        'chat:message',
        {'contact_id': recipient.id, 'message': _chat_message_payload(message, sender.id)},
        to=_chat_user_room(sender.id),
    )
    socketio.emit(
        'chat:message',
        {'contact_id': sender.id, 'message': _chat_message_payload(message, recipient.id)},
        to=_chat_user_room(recipient.id),
    )
    _emit_chat_unread_count(recipient.id)
    _emit_chat_contacts_changed((sender.id, recipient.id))
    preview = ' '.join(message.body.split())[:160]
    _notify_user(recipient, f'New message from {sender.full_name}', 'You have a new chat message', f'{sender.full_name} sent you a message: {preview}', 'chat')


@socketio.on('connect')
def chat_socket_connect():
    user_id = session.get('user', {}).get('id')
    user = User.query.filter_by(id=user_id, is_active=True).first() if user_id else None
    if not user:
        return False
    join_room(_chat_user_room(user.id))
    with CHAT_PRESENCE_LOCK:
        became_online = CHAT_CONNECTED_USERS.get(user.id, 0) == 0
        CHAT_CONNECTED_USERS[user.id] = CHAT_CONNECTED_USERS.get(user.id, 0) + 1
    _touch_chat_presence(user)
    if became_online:
        _emit_chat_presence(user)
    _emit_chat_unread_count(user.id)


@socketio.on('disconnect')
def chat_socket_disconnect():
    user_id = session.get('user', {}).get('id')
    if not user_id:
        return
    became_offline = False
    with CHAT_PRESENCE_LOCK:
        connections = max(0, CHAT_CONNECTED_USERS.get(user_id, 1) - 1)
        if connections:
            CHAT_CONNECTED_USERS[user_id] = connections
        else:
            CHAT_CONNECTED_USERS.pop(user_id, None)
            became_offline = True
    if became_offline:
        user = db.session.get(User, user_id)
        if user:
            _emit_chat_presence(user)


@socketio.on('chat:send')
def chat_socket_send(payload):
    current_user = db.session.get(User, session.get('user', {}).get('id'))
    contact_id = payload.get('contact_id') if isinstance(payload, dict) else None
    body = (payload.get('body') or '').strip() if isinstance(payload, dict) else ''
    if not current_user or not isinstance(contact_id, int) or not body or len(body) > 2000:
        return {'ok': False, 'error': 'Messages must be between 1 and 2,000 characters.'}
    message, recipient = _create_chat_message(current_user, contact_id, body)
    if not message:
        return {'ok': False, 'error': 'This conversation is no longer available.'}
    _publish_chat_message(message, current_user, recipient)
    return {'ok': True}


@socketio.on('chat:read')
def chat_socket_read(payload):
    current_user = db.session.get(User, session.get('user', {}).get('id'))
    contact_id = payload.get('contact_id') if isinstance(payload, dict) else None
    if not current_user or not isinstance(contact_id, int) or not _permitted_chat_contact(current_user, contact_id):
        return {'ok': False}
    _mark_chat_conversation_read(current_user, contact_id)
    return {'ok': True}


@app.route('/chat')
@login_required
def chat():
    """Role-aware real-time messaging workspace."""
    current_user = User.query.get_or_404(session['user']['id'])
    _touch_chat_presence(current_user)
    return render_template('dashboard/chat.html', is_dashboard=True)


@app.route('/api/chat/contacts')
@login_required
def api_chat_contacts():
    current_user = User.query.get_or_404(session['user']['id'])
    _touch_chat_presence(current_user)
    contacts = [_chat_contact_payload(contact, current_user.id) for contact in _chat_contacts_for(current_user).values()]
    contacts.sort(key=lambda contact: (contact['last_message_at'] is None, contact['last_message_at'] or '', contact['name']), reverse=True)
    return jsonify({'contacts': contacts, 'total_unread': sum(contact['unread_count'] for contact in contacts)})


@app.route('/api/chat/unread-count')
@login_required
def api_chat_unread_count():
    """Return the current user's unread total for the persistent sidebar badge."""
    return jsonify({'total_unread': _unread_chat_count(session['user']['id'])})


@app.route('/api/chat/conversations/<int:contact_id>')
@login_required
def api_chat_conversation(contact_id):
    current_user = User.query.get_or_404(session['user']['id'])
    contact = _permitted_chat_contact(current_user, contact_id)
    if not contact:
        return jsonify({'error': 'This conversation is no longer available.'}), 403

    _touch_chat_presence(current_user)
    conversation = _conversation_for_users(current_user.id, contact_id)
    messages = conversation.messages if conversation else []
    _mark_chat_conversation_read(current_user, contact_id)
    return jsonify({
        'contact': _chat_contact_payload(contact, current_user.id),
        'messages': [_chat_message_payload(message, current_user.id) for message in messages],
    })


@app.route('/api/chat/conversations/<int:contact_id>/messages', methods=['POST'])
@login_required
def api_send_chat_message(contact_id):
    current_user = User.query.get_or_404(session['user']['id'])
    if not _permitted_chat_contact(current_user, contact_id):
        return jsonify({'error': 'This conversation is no longer available.'}), 403

    payload = request.get_json(silent=True) or {}
    body = (payload.get('body') or '').strip()
    if not body or len(body) > 2000:
        return jsonify({'error': 'Messages must be between 1 and 2,000 characters.'}), 400

    message, recipient = _create_chat_message(current_user, contact_id, body)
    if not message:
        return jsonify({'error': 'This conversation is no longer available.'}), 403
    _publish_chat_message(message, current_user, recipient)
    return jsonify({'message': _chat_message_payload(message, current_user.id)}), 201

@app.route('/quotations', methods=['GET', 'POST'])
@login_required
@role_required(['admin', 'client'])
def quotations():
    """List quotations owned by a client or all quotations for an admin."""
    return render_template('dashboard/document_list.html', is_dashboard=True, documents=_documents_for_current_user(Quotation), document_label='Quotations', detail_path_prefix='/quotations', date_label='Generated', icon='file-text', icon_background='bg-sahal-cream', amount_class='text-sahal-blue', badge_class='bg-blue-100 text-sahal-blue', border_class='border-gray-200 hover:border-sahal-blue/30', empty_message='A quotation is generated automatically when an order is submitted.')


@app.route('/quotations/<int:quotation_id>')
@login_required
@role_required(['admin', 'client'])
def quotation_detail(quotation_id):
    """Show a quotation to its client owner or an administrator."""
    quotation = _document_for_current_user(Quotation, quotation_id)
    return render_template('dashboard/quotation_detail.html', is_dashboard=True, quotation=quotation)


@app.route('/quotations/<int:quotation_id>/download')
@login_required
@role_required(['admin', 'client'])
def quotation_download(quotation_id):
    """Download a client-owned or admin-visible quotation as an A4 PDF."""
    quotation = _document_for_current_user(Quotation, quotation_id)
    return send_file(
        _build_quotation_pdf(quotation),
        as_attachment=True,
        download_name=f'{quotation.reference}.pdf',
        mimetype='application/pdf',
    )


@app.route('/invoices')
@login_required
@role_required(['admin', 'client'])
def invoices():
    """List invoices owned by a client or all invoices for an admin."""
    return render_template('dashboard/document_list.html', is_dashboard=True, documents=_documents_for_current_user(Invoice), document_label='Invoices', detail_path_prefix='/invoices', date_label='Issued', icon='receipt', icon_background='bg-sahal-blue/10', amount_class='text-sahal-blue', badge_class='bg-blue-100 text-sahal-blue', border_class='border-sahal-blue/20 hover:border-sahal-blue/50', empty_message='An invoice is issued automatically when assigned staff complete an order.')


@app.route('/invoices/<int:invoice_id>')
@login_required
@role_required(['admin', 'client'])
def invoice_detail(invoice_id):
    """Show an invoice to its client owner or an administrator."""
    invoice = _document_for_current_user(Invoice, invoice_id)
    return render_template('dashboard/invoice_detail.html', is_dashboard=True, invoice=invoice)


@app.route('/invoices/<int:invoice_id>/download')
@login_required
@role_required(['admin', 'client'])
def invoice_download(invoice_id):
    """Download a client-owned or admin-visible invoice as an A4 PDF."""
    invoice = _document_for_current_user(Invoice, invoice_id)
    return send_file(
        _build_invoice_pdf(invoice),
        as_attachment=True,
        download_name=f'{invoice.reference}.pdf',
        mimetype='application/pdf',
    )


@app.route('/receipts')
@login_required
@role_required(['admin', 'client'])
def receipts():
    """List receipts owned by a client or all receipts for an admin."""
    return render_template('dashboard/document_list.html', is_dashboard=True, documents=_documents_for_current_user(Receipt), document_label='Receipts', detail_path_prefix='/receipts', date_label='Delivered', icon='badge-check', icon_background='bg-gray-200', amount_class='text-gray-900', badge_class='bg-gray-200 text-gray-800', border_class='border-gray-300 hover:border-gray-500', empty_message='A delivery receipt is issued automatically when an administrator marks a completed order delivered.')


@app.route('/receipts/<int:receipt_id>')
@login_required
@role_required(['admin', 'client'])
def receipt_detail(receipt_id):
    """Show a receipt to its client owner or an administrator."""
    receipt = _document_for_current_user(Receipt, receipt_id)
    return render_template('dashboard/receipt_detail.html', is_dashboard=True, receipt=receipt)


@app.route('/receipts/<int:receipt_id>/download')
@login_required
@role_required(['admin', 'client'])
def receipt_download(receipt_id):
    """Download a client-owned or admin-visible delivery receipt as an A4 PDF."""
    receipt = _document_for_current_user(Receipt, receipt_id)
    return send_file(
        _build_receipt_pdf(receipt),
        as_attachment=True,
        download_name=f'{receipt.reference}.pdf',
        mimetype='application/pdf',
    )


def _documents_for_current_user(model):
    """Return system-wide documents for admins and owned documents for clients."""
    query = model.query
    if session['user']['role'] != 'admin':
        query = query.filter_by(user_id=session['user']['id'])
    return query.order_by(model.created_at.desc()).all()


def _order_client_address(order):
    """Prefer an address captured on the order, falling back to the client profile."""
    for item in order.items:
        address = item.selected_options_dict.get('Billing') or item.selected_options_dict.get('Shipping')
        if address:
            return address
    return order.user.address or None


def _document_for_current_user(model, document_id):
    query = model.query.filter_by(id=document_id)
    if session['user']['role'] != 'admin':
        query = query.filter_by(user_id=session['user']['id'])
    return query.first_or_404()


DOCUMENT_MODELS = {'quotation': Quotation, 'invoice': Invoice, 'receipt': Receipt}
DOCUMENT_LABELS = {'quotation': 'Quotation', 'invoice': 'Invoice', 'receipt': 'Receipt'}


def _manual_document_total(line_items, tax_rate, discount):
    subtotal = sum((item['subtotal'] for item in line_items), Decimal('0.00'))
    tax_amount = (subtotal * tax_rate / Decimal('100')).quantize(Decimal('0.01'))
    total = max(Decimal('0.00'), subtotal + tax_amount - discount).quantize(Decimal('0.01'))
    return subtotal, tax_amount, total


def _manual_document_draft(form):
    document_type = form.get('document_type', 'quotation')
    if document_type not in DOCUMENT_MODELS:
        return None
    line_items = []
    for service_id, quantity, rate in zip(form.getlist('service_id'), form.getlist('quantity'), form.getlist('unit_price')):
        service = Service.query.get(int(service_id)) if service_id.isdigit() else None
        try:
            qty = max(1, int(quantity))
            unit_price = max(Decimal('0.00'), Decimal(rate))
        except (InvalidOperation, ValueError):
            continue
        if service:
            line_items.append({'service_id': service.id, 'name': service.name, 'quantity': qty, 'unit_price': unit_price, 'subtotal': (unit_price * qty).quantize(Decimal('0.01'))})
    if not form.get('full_name', '').strip() or not form.get('email', '').strip() or not line_items:
        return None
    try:
        tax_rate = max(Decimal('0.00'), Decimal(form.get('tax_rate', '0')))
        discount = max(Decimal('0.00'), Decimal(form.get('discount', '0')))
    except InvalidOperation:
        return None
    subtotal, tax_amount, total = _manual_document_total(line_items, tax_rate, discount)
    return {'document_type': document_type, 'full_name': form['full_name'].strip(), 'email': form['email'].strip().lower(), 'phone_number': form.get('phone_number', '').strip(), 'account_type': form.get('account_type', 'personal'), 'company_name': form.get('company_name', '').strip(), 'company_website': form.get('company_website', '').strip(), 'billing_address': form.get('billing_address', '').strip(), 'line_items': line_items, 'subtotal': subtotal, 'tax_rate': tax_rate, 'tax_amount': tax_amount, 'discount': discount, 'total': total}


def _build_manual_document_pdf(draft):
    """Render a preview-stage walk-in document without committing it to the database."""
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    page_width, page_height = A4
    margin = 18 * mm
    pdf.setFillColor(colors.HexColor('#002766'))
    pdf.rect(0, page_height - 48 * mm, page_width, 48 * mm, fill=1, stroke=0)
    logo_path = os.path.join(app.root_path, 'static', 'images', 'Logo White.png')
    if os.path.exists(logo_path):
        pdf.drawImage(logo_path, margin, page_height - 30 * mm, width=44 * mm, height=15 * mm, preserveAspectRatio=True, mask='auto')
    pdf.setFillColor(colors.white)
    pdf.setFont('Helvetica-Bold', 12)
    pdf.drawRightString(page_width - margin, page_height - 22 * mm, f'{DOCUMENT_LABELS[draft["document_type"]].upper()} PREVIEW')
    y = page_height - 64 * mm
    pdf.setFillColor(colors.HexColor('#111827'))
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawString(margin, y, 'PREPARED FOR')
    y -= 6 * mm
    pdf.setFont('Helvetica-Bold', 11)
    pdf.drawString(margin, y, draft['full_name'])
    pdf.setFont('Helvetica', 9)
    y -= 5 * mm
    pdf.drawString(margin, y, draft['email'])
    y -= 14 * mm
    pdf.setFillColor(colors.HexColor('#003399'))
    pdf.rect(margin, y - 8 * mm, page_width - 2 * margin, 10 * mm, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont('Helvetica-Bold', 8)
    pdf.drawString(margin + 3 * mm, y - 4 * mm, 'DESCRIPTION')
    pdf.drawRightString(page_width - margin - 45 * mm, y - 4 * mm, 'QTY')
    pdf.drawRightString(page_width - margin - 22 * mm, y - 4 * mm, 'RATE')
    pdf.drawRightString(page_width - margin, y - 4 * mm, 'AMOUNT')
    y -= 15 * mm
    for item in draft['line_items']:
        pdf.setFillColor(colors.HexColor('#111827'))
        pdf.setFont('Helvetica', 9)
        pdf.drawString(margin + 3 * mm, y, item['name'][:56])
        pdf.drawRightString(page_width - margin - 45 * mm, y, str(item['quantity']))
        pdf.drawRightString(page_width - margin - 22 * mm, y, f'${Decimal(item["unit_price"]):,.2f}')
        pdf.drawRightString(page_width - margin, y, f'${Decimal(item["subtotal"]):,.2f}')
        y -= 9 * mm
    y -= 5 * mm
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawRightString(page_width - margin - 35 * mm, y, 'TOTAL')
    pdf.setFillColor(colors.HexColor('#003399'))
    pdf.setFont('Helvetica-Bold', 17)
    pdf.drawRightString(page_width - margin, y - 2 * mm, f'${Decimal(draft["total"]):,.2f}')
    pdf.save()
    output.seek(0)
    return output


@app.route('/admin/documents/create')
@login_required
@role_required('admin')
def admin_document_create():
    document_type = request.args.get('type', 'quotation')
    if document_type not in DOCUMENT_MODELS:
        return redirect(url_for('quotations'))
    quotations_data = Quotation.query.order_by(Quotation.created_at.desc()).all() if document_type in ('invoice', 'receipt') else []
    prefill = {}
    talent_request_id = request.args.get('talent_request', type=int)
    if talent_request_id:
        booking_request = TalentBookingRequest.query.get_or_404(talent_request_id)
        prefill = {
            'full_name': booking_request.full_name,
            'email': booking_request.email,
            'phone_number': booking_request.phone_number or '',
            'company_name': booking_request.company_name or '',
            'billing_address': ', '.join(value for value in (booking_request.city, booking_request.venue_details) if value),
        }
    return render_template('dashboard/admin_document_form.html', is_dashboard=True, document_type=document_type, document_label=DOCUMENT_LABELS[document_type], services=Service.query.filter_by(is_active=True).order_by(Service.name).all(), quotations=quotations_data, prefill=prefill)


@app.route('/admin/documents/quotations/<int:quotation_id>/import')
@login_required
@role_required('admin')
def admin_document_quotation_import(quotation_id):
    """Return client and item data needed to draft an invoice or receipt from a quotation."""
    quotation = Quotation.query.get_or_404(quotation_id)
    return jsonify({
        'reference': quotation.reference,
        'client': {
            'full_name': quotation.user.full_name,
            'email': quotation.user.email,
            'phone_number': quotation.user.phone_number or '',
            'account_type': quotation.user.account_type or 'personal',
            'company_name': quotation.user.company_name or '',
            'company_website': quotation.user.company_website or '',
            'billing_address': '',
        },
        'items': [
            {'service_id': item.service_id, 'quantity': item.quantity, 'unit_price': str(item.unit_price)}
            for item in quotation.order.items
        ],
        'tax_rate': '0',
        'discount': '0',
    })


@app.route('/admin/documents/preview', methods=['POST'])
@login_required
@role_required('admin')
def admin_document_preview():
    draft = _manual_document_draft(request.form)
    if not draft:
        flash('Add client details and at least one valid catalog item before previewing.', 'error')
        return redirect(url_for('admin_document_create', type=request.form.get('document_type', 'quotation')))
    session['manual_document_draft'] = {**draft, 'line_items': [{**item, 'unit_price': str(item['unit_price']), 'subtotal': str(item['subtotal'])} for item in draft['line_items']], 'subtotal': str(draft['subtotal']), 'tax_rate': str(draft['tax_rate']), 'tax_amount': str(draft['tax_amount']), 'discount': str(draft['discount']), 'total': str(draft['total'])}
    return render_template('dashboard/admin_document_preview.html', is_dashboard=True, draft=draft, document_label=DOCUMENT_LABELS[draft['document_type']])


@app.route('/admin/documents/preview/download')
@login_required
@role_required('admin')
def admin_document_preview_download():
    """Download the current unsaved walk-in document preview as an A4 PDF."""
    draft = session.get('manual_document_draft')
    if not draft:
        return redirect(url_for('quotations'))
    return send_file(_build_manual_document_pdf(draft), as_attachment=True, download_name=f'{DOCUMENT_LABELS[draft["document_type"]]}-preview.pdf', mimetype='application/pdf')


@app.route('/admin/documents/save', methods=['POST'])
@login_required
@role_required('admin')
def admin_document_save():
    draft = session.pop('manual_document_draft', None)
    if not draft:
        flash('Your document draft expired. Please create it again.', 'error')
        return redirect(url_for('quotations'))
    user = User.query.filter_by(email=draft['email']).first()
    if user is None:
        user = User(full_name=draft['full_name'], email=draft['email'], role='client', account_type=draft['account_type'], phone_number=draft['phone_number'] or None, company_name=draft['company_name'] or None, company_website=draft['company_website'] or None, is_active=True)
        user.set_password(os.urandom(16).hex())
        db.session.add(user)
        db.session.flush()
    else:
        user.full_name = draft['full_name']
        user.phone_number = draft['phone_number'] or user.phone_number
        user.company_name = draft['company_name'] or user.company_name
        user.company_website = draft['company_website'] or user.company_website
    document_type = draft['document_type']
    status = {'quotation': 'placed', 'invoice': 'fulfilled', 'receipt': 'dispatched'}[document_type]
    now = datetime.utcnow()
    order = Order(user_id=user.id, status=status, total_price=Decimal(draft['total']), created_at=now)
    if document_type in ('invoice', 'receipt'):
        order.fulfilled_at = now
    if document_type == 'receipt':
        order.dispatched_at = now
    db.session.add(order)
    db.session.flush()
    for item in draft['line_items']:
        db.session.add(OrderItem(order_id=order.id, service_id=item['service_id'], quantity=item['quantity'], unit_price=Decimal(item['unit_price']), subtotal=Decimal(item['subtotal']), selected_options=json.dumps({'Billing': draft['billing_address']} if draft['billing_address'] else {})))
    document_model = DOCUMENT_MODELS[document_type]
    prefix = {'quotation': 'QT', 'invoice': 'INV', 'receipt': 'RCT'}[document_type]
    document = document_model(order_id=order.id, user_id=user.id, reference=f'{prefix}-{now.strftime("%Y%m%d")}-{order.id:05d}', total_price=Decimal(draft['total']))
    db.session.add(document)
    db.session.commit()
    _notify_user(user, f'{DOCUMENT_LABELS[document_type]} {document.reference} is ready', f'Your {DOCUMENT_LABELS[document_type].lower()} is ready', f'A new {DOCUMENT_LABELS[document_type].lower()} has been created for you.', f'{document_type}_detail', **{f'{document_type}_id': document.id})
    flash(f'{DOCUMENT_LABELS[document_type]} created successfully.', 'success')
    return redirect(f'/{document_type}s/{document.id}' if document_type != 'quotation' else f'/quotations/{document.id}')

@app.route('/bookings')
@login_required
def bookings():
    """Bookings management"""
    return redirect(url_for('dashboard'))  # Placeholder

# ===========================
# API ROUTES (JSON)
# ===========================

@app.route('/api/talent', methods=['GET'])
def api_talent():
    """Get talent data (API endpoint)"""
    talents = [{'id': i, 'name': f'Talent {i}', 'category': 'Actor'} for i in range(1, 25)]
    return jsonify(talents)

@app.route('/api/products', methods=['GET'])
def api_products():
    """Get products data (API endpoint)"""
    products = [{'id': i, 'name': f'Product {i}', 'price': 50 + i*10} for i in range(1, 25)]
    return jsonify(products)

@app.route('/api/quotation', methods=['POST'])
@login_required
def api_create_quotation():
    """Create quotation via API"""
    data = request.get_json()
    # Handle quotation creation
    return jsonify({'success': True, 'message': 'Quotation created', 'id': '#QT-2024-1234'})

# ===========================
# ERROR HANDLERS
# ===========================

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    return render_template('errors/500.html'), 500

# ===========================
# UTILITY ROUTES
# ===========================

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'app': 'SAHAL System'
    })

# ===========================
# Template Filters (Optional Jinja2 Enhancements)
# ===========================

@app.template_filter('currency')
def currency_filter(value):
    """Format value as currency"""
    try:
        return f"${value:,.2f}"
    except:
        return value

@app.template_filter('date_format')
def date_format_filter(value, format='%b %d, %Y'):
    """Format date string"""
    if isinstance(value, str):
        return value
    return value.strftime(format) if value else ''

# ===========================
# Run Application
# ===========================

if __name__ == '__main__':
    # Ensure required directories exist
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    
    # This entry point is for local development. Deploy production with a
    # dedicated Socket.IO-capable WSGI/ASGI server and FLASK_ENV=production.
    socketio.run(
        app,
        host='0.0.0.0',
        port=5000,
        debug=app.config['DEBUG'],
        use_reloader=app.config['DEBUG']
    )

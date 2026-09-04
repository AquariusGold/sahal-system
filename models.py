"""SAHAL System - Database Models

NOTE: The `users` table already exists in the shared `sahal_db` MySQL database
(managed via Alembic elsewhere) with real data. This model mirrors that exact
schema - do not add/rename columns here without a matching migration.
"""
import json
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db

# Role values stored in the DB enum. 'client' is used for what the product
# refers to as the "Normal User" role.
ROLES = ('admin', 'staff', 'client')
ROLE_LABELS = {'admin': 'Admin', 'staff': 'Staff', 'client': 'Normal User'}


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.Enum('admin', 'staff', 'client', name='role'), nullable=False, default='client')
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    account_type = db.Column(db.Enum('personal', 'business', name='account_type'), nullable=False, default='personal')
    phone_number = db.Column(db.String(20))
    company_name = db.Column(db.String(120))
    company_website = db.Column(db.String(255))
    industry = db.Column(db.String(120))
    avatar_url = db.Column(db.String(255))
    job_title = db.Column(db.String(120))
    location = db.Column(db.String(120))
    bio = db.Column(db.Text)
    last_seen_at = db.Column(db.DateTime)
    theme_preference = db.Column(db.String(10), nullable=False, default='light')
    address = db.Column(db.String(255))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def initials(self):
        parts = self.full_name.split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return self.full_name[:2].upper() if self.full_name else 'U'

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role.capitalize())

    def to_session_dict(self):
        return {
            'id': self.id,
            'name': self.full_name,
            'initials': self.initials,
            'email': self.email,
            'role': self.role,
            'avatar_url': self.avatar_url,
            'theme_preference': self.theme_preference or 'light',
            'is_authenticated': True,
        }


class ServiceCategory(db.Model):
    """Products & Services category. Backed by the existing `sahal_service_categories`
    table (managed via Alembic elsewhere); `icon` was added additively for the
    admin catalog manager."""
    __tablename__ = 'sahal_service_categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    json_id = db.Column(db.String(100), unique=True)
    slug = db.Column(db.String(200), nullable=False, unique=True)
    order = db.Column(db.Integer, nullable=False, default=0)
    meta_title = db.Column(db.String(150))
    meta_description = db.Column(db.Text)
    icon = db.Column(db.String(50), default='package')

    services = db.relationship(
        'Service', backref='category', lazy=True,
        order_by='Service.order', cascade='all, delete-orphan'
    )


class Service(db.Model):
    """Product/service catalog entry. Backed by the existing `sahal_services`
    table; `image_url` was added additively for the admin catalog manager."""
    __tablename__ = 'sahal_services'

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('sahal_service_categories.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=False, default='')
    base_price = db.Column(db.Numeric(10, 2))
    pricing_type = db.Column(db.Enum('fixed', 'per_unit', 'custom', name='pricing_type'), nullable=False, default='fixed')
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    json_id = db.Column(db.String(100), unique=True)
    turnaround_time = db.Column(db.String(100))
    deliverables_list = db.Column(db.Text)
    slug = db.Column(db.String(200), nullable=False, unique=True)
    order = db.Column(db.Integer, nullable=False, default=0)
    meta_title = db.Column(db.String(150))
    meta_description = db.Column(db.Text)
    image_url = db.Column(db.String(255))
    long_description = db.Column(db.Text)
    specifications = db.Column(db.Text)  # JSON list of {"label": ..., "value": ...}
    options = db.Column(db.Text)  # JSON list of {"name": ..., "values": [...]}

    @property
    def price_display(self):
        if self.pricing_type == 'custom' or self.base_price is None:
            return 'Custom Quote'
        return f"{self.base_price:.2f}"

    @property
    def specifications_list(self):
        try:
            return json.loads(self.specifications) if self.specifications else []
        except (ValueError, TypeError):
            return []

    @property
    def options_list(self):
        try:
            return json.loads(self.options) if self.options else []
        except (ValueError, TypeError):
            return []


def specs_to_text(specs_list):
    """Render a specifications list back to 'Label: Value' lines for the admin form."""
    return '\n'.join(f"{s.get('label', '')}: {s.get('value', '')}" for s in (specs_list or []))


def text_to_specs(text):
    """Parse 'Label: Value' lines (one per line) into a specifications list."""
    specs = []
    for line in (text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        label, _, value = line.partition(':')
        specs.append({'label': label.strip(), 'value': value.strip()})
    return specs


def options_to_text(options_list):
    """Render an options list back to 'Name: value1, value2' lines for the admin form."""
    return '\n'.join(f"{o.get('name', '')}: {', '.join(o.get('values', []))}" for o in (options_list or []))


def text_to_options(text):
    """Parse 'Name: value1, value2' lines (one per line) into an options list."""
    options = []
    for line in (text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, values = line.partition(':')
        values_list = [v.strip() for v in values.split(',') if v.strip()]
        if name.strip() and values_list:
            options.append({'name': name.strip(), 'values': values_list})
    return options


class Order(db.Model):
    """Customer order. Backed by the existing `orders` table."""
    __tablename__ = 'orders'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(
        db.Enum('placed', 'assigned', 'accepted', 'fulfilled', 'dispatched', 'received', 'cancelled', name='order_status'),
        nullable=False, default='placed'
    )
    total_price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    assigned_staff_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    assigned_at = db.Column(db.DateTime)
    accepted_at = db.Column(db.DateTime)
    fulfilled_at = db.Column(db.DateTime)
    dispatched_at = db.Column(db.DateTime)
    received_at = db.Column(db.DateTime)

    items = db.relationship('OrderItem', backref='order', lazy=True, cascade='all, delete-orphan')
    user = db.relationship('User', backref='orders', lazy=True, foreign_keys=[user_id])
    assigned_staff = db.relationship('User', lazy=True, foreign_keys=[assigned_staff_id])

    STAGE_LABELS = {
        'placed': 'Order Placed',
        'assigned': 'Order Assigned',
        'accepted': 'Job Accepted',
        'fulfilled': 'Fulfillment Complete',
        'dispatched': 'Dispatch / Delivery',
        'received': 'Receipt Confirmed',
        'cancelled': 'Cancelled',
    }

    @property
    def timeline(self):
        """Ordered list of the 6 lifecycle stages with their timestamp (or None if not reached yet)."""
        return [
            {'key': 'placed', 'label': 'Order Placed', 'timestamp': self.created_at},
            {'key': 'assigned', 'label': 'Order Assigned', 'timestamp': self.assigned_at},
            {'key': 'accepted', 'label': 'Job Accepted', 'timestamp': self.accepted_at},
            {'key': 'fulfilled', 'label': 'Fulfillment Complete', 'timestamp': self.fulfilled_at},
            {'key': 'dispatched', 'label': 'Dispatch / Delivery', 'timestamp': self.dispatched_at},
            {'key': 'received', 'label': 'Receipt Confirmed', 'timestamp': self.received_at},
        ]

    @property
    def status_label(self):
        return self.STAGE_LABELS.get(self.status, self.status.capitalize())


class OrderItem(db.Model):
    """Line item within an order. Backed by the existing `order_items` table;
    `selected_options` was added additively to record the buyer's option picks."""
    __tablename__ = 'order_items'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey('sahal_services.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    selected_options = db.Column(db.Text)  # JSON dict of {option_name: selected_value}

    service = db.relationship('Service', lazy=True)

    @property
    def selected_options_dict(self):
        try:
            return json.loads(self.selected_options) if self.selected_options else {}
        except (ValueError, TypeError):
            return {}


class Quotation(db.Model):
    """Automatically generated client quotation for a submitted order."""
    __tablename__ = 'quotations'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    reference = db.Column(db.String(30), nullable=False, unique=True)
    status = db.Column(db.Enum('generated', 'accepted', 'expired', name='quotation_status'), nullable=False, default='generated')
    total_price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    order = db.relationship('Order', backref=db.backref('quotation', uselist=False), lazy=True)
    user = db.relationship('User', backref='quotations', lazy=True)


class Invoice(db.Model):
    """Invoice generated once an accepted order has been completed by staff."""
    __tablename__ = 'invoices'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    reference = db.Column(db.String(30), nullable=False, unique=True)
    status = db.Column(db.Enum('issued', 'paid', 'overdue', name='invoice_status'), nullable=False, default='issued')
    total_price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    order = db.relationship('Order', backref=db.backref('invoice', uselist=False), lazy=True)
    user = db.relationship('User', backref='invoices', lazy=True)


class Receipt(db.Model):
    """Receipt generated when an administrator marks a fulfilled order delivered."""
    __tablename__ = 'receipts'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    reference = db.Column(db.String(30), nullable=False, unique=True)
    total_price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    order = db.relationship('Order', backref=db.backref('receipt', uselist=False), lazy=True)
    user = db.relationship('User', backref='receipts', lazy=True)


class Conversation(db.Model):
    """A direct message thread between two SAHAL users."""
    __tablename__ = 'conversations'

    id = db.Column(db.Integer, primary_key=True)
    participant_one_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    participant_two_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    participant_one = db.relationship('User', foreign_keys=[participant_one_id])
    participant_two = db.relationship('User', foreign_keys=[participant_two_id])
    messages = db.relationship('ChatMessage', backref='conversation', lazy=True, cascade='all, delete-orphan', order_by='ChatMessage.created_at')

    __table_args__ = (
        db.UniqueConstraint('participant_one_id', 'participant_two_id', name='uq_conversation_participants'),
        db.Index('ix_conversations_updated_at', 'updated_at'),
    )


class ChatMessage(db.Model):
    """A persisted direct message with per-recipient read tracking."""
    __tablename__ = 'chat_messages'

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id'), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    read_at = db.Column(db.DateTime)

    sender = db.relationship('User', foreign_keys=[sender_id])

    __table_args__ = (
        db.Index('ix_chat_messages_conversation_read_sender', 'conversation_id', 'read_at', 'sender_id'),
        db.Index('ix_chat_messages_conversation_created', 'conversation_id', 'created_at'),
    )


class SahalInquiry(db.Model):
    """Detailed project enquiry submitted from the public Sahal contact page."""
    __tablename__ = 'sahal_inquiries'

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone_number = db.Column(db.String(40))
    company_name = db.Column(db.String(120))
    company_website = db.Column(db.String(255))
    services = db.Column(db.String(255), nullable=False)
    project_title = db.Column(db.String(160))
    project_goal = db.Column(db.Text)
    target_audience = db.Column(db.Text)
    deliverables = db.Column(db.Text)
    timeline = db.Column(db.String(80))
    budget_range = db.Column(db.String(80))
    contact_preference = db.Column(db.String(30))
    project_details = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


TALENT_CATEGORIES = ('Actors', 'Models', 'Influencers', 'Musicians', 'Dancers')


class Talent(db.Model):
    """Talent Agency profile, managed entirely from the Admin Dashboard."""
    __tablename__ = 'talent_profiles'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(50), nullable=False, default='Actors')
    bio = db.Column(db.Text, default='')
    location = db.Column(db.String(150), default='')
    photo_url = db.Column(db.String(255))
    photos = db.Column(db.Integer, nullable=False, default=4)
    rating = db.Column(db.Numeric(2, 1), nullable=False, default=4.5)
    reviews = db.Column(db.Integer, nullable=False, default=0)
    followers = db.Column(db.Integer, nullable=False, default=0)
    bookings = db.Column(db.Integer, nullable=False, default=0)
    featured = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    gallery_images = db.relationship(
        'TalentGalleryImage', backref='talent', lazy=True,
        order_by='TalentGalleryImage.order', cascade='all, delete-orphan'
    )
    booking_requests = db.relationship(
        'TalentBookingRequest', backref='talent', lazy=True,
        order_by='TalentBookingRequest.created_at.desc()', cascade='all, delete-orphan'
    )


class TalentGalleryImage(db.Model):
    """An image displayed in a talent profile gallery."""
    __tablename__ = 'talent_gallery_images'

    id = db.Column(db.Integer, primary_key=True)
    talent_id = db.Column(db.Integer, db.ForeignKey('talent_profiles.id'), nullable=False)
    image_url = db.Column(db.String(255), nullable=False)
    order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class TalentBookingRequest(db.Model):
    """A client booking or quote request submitted from a talent profile."""
    __tablename__ = 'talent_booking_requests'

    id = db.Column(db.Integer, primary_key=True)
    talent_id = db.Column(db.Integer, db.ForeignKey('talent_profiles.id'), nullable=False, index=True)
    client_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    assigned_staff_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    full_name = db.Column(db.String(120), nullable=False)
    company_name = db.Column(db.String(120))
    email = db.Column(db.String(120), nullable=False)
    phone_number = db.Column(db.String(40))
    project_name = db.Column(db.String(180), nullable=False)
    budget_range = db.Column(db.String(100), nullable=False)
    event_start_date = db.Column(db.Date)
    event_end_date = db.Column(db.Date)
    duration = db.Column(db.String(100))
    city = db.Column(db.String(120))
    venue_details = db.Column(db.String(255))
    location_mode = db.Column(db.String(30))
    category = db.Column(db.String(50), nullable=False)
    category_details = db.Column(db.Text, nullable=False, default='{}')
    attachment_url = db.Column(db.String(255))
    status = db.Column(
        db.Enum('new', 'in_review', 'approved', 'declined', 'completed', name='talent_request_status'),
        nullable=False, default='new', index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    client_user = db.relationship('User', foreign_keys=[client_user_id])
    assigned_staff = db.relationship('User', foreign_keys=[assigned_staff_id])

    STATUS_LABELS = {
        'new': 'New',
        'in_review': 'In Review',
        'approved': 'Approved',
        'declined': 'Declined',
        'completed': 'Completed',
    }

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, self.status.replace('_', ' ').title())

    @property
    def category_details_dict(self):
        try:
            return json.loads(self.category_details) if self.category_details else {}
        except (ValueError, TypeError):
            return {}


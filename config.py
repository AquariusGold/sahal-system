"""
SAHAL System - Flask Configuration
Handles different environment configurations (dev, testing, production)
"""

import os
from datetime import timedelta

class Config:
    """Base configuration"""
    
    # Flask settings
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'development-only-secret-key'
    DEBUG = False
    TESTING = False
    
    # Session settings
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_REFRESH_EACH_REQUEST = False
    
    # Application settings
    JSON_SORT_KEYS = False
    PROPAGATE_EXCEPTIONS = True
    
    # File uploads
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
    UPLOAD_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'pdf', 'doc', 'docx']
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', 'products')
    
    # Database (MySQL)
    MYSQL_HOST = os.environ.get('MYSQL_HOST', 'localhost')
    MYSQL_PORT = int(os.environ.get('MYSQL_PORT', 3306))
    MYSQL_USER = os.environ.get('MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', '')
    MYSQL_DB = os.environ.get('MYSQL_DB', 'sahal_db')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or (
        f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')
    AUTO_SCHEMA_MANAGEMENT = False

    # Outbound email. Leave MAIL_SERVER empty to disable delivery safely in local environments.
    MAIL_SERVER = os.environ.get('MAIL_SERVER', '')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() in {'1', 'true', 'yes', 'on'}
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'false').lower() in {'1', 'true', 'yes', 'on'}
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER') or MAIL_USERNAME
    MAIL_FROM_NAME = os.environ.get('MAIL_FROM_NAME', 'SAHAL Branding Agency')
    APP_BASE_URL = os.environ.get('APP_BASE_URL', 'https://sahalbrandingagency.com').rstrip('/')

    # Admins are created explicitly with the `flask create-admin` command.


class DevelopmentConfig(Config):
    """Development configuration"""
    
    DEBUG = True
    TESTING = False
    
    # Less strict session settings for development
    SESSION_COOKIE_SECURE = False
    
    # Database
    SQLALCHEMY_ECHO = True
    AUTO_SCHEMA_MANAGEMENT = True
    
    # Flask-DebugToolbar
    DEBUG_TB_ENABLED = True


class TestingConfig(Config):
    """Testing configuration"""
    
    DEBUG = True
    TESTING = True
    
    # Disable CSRF for testing
    WTF_CSRF_ENABLED = False
    
    # Use in-memory database for tests
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


class ProductionConfig(Config):
    """Production configuration"""
    
    DEBUG = False
    TESTING = False
    
    # Strict security settings
    SESSION_COOKIE_SECURE = True
    
    SQLALCHEMY_DATABASE_URI = None
    RATELIMIT_STORAGE_URI = None

    @classmethod
    def init_app(cls, app):
        """Load production-only secrets only after production is selected."""
        def required_env(name):
            value = os.environ[name]
            if not value.strip():
                raise RuntimeError(f'{name} must be set to a non-empty value in production.')
            return value

        app.config['SECRET_KEY'] = required_env('SECRET_KEY')
        app.config['SUPER_ADMIN_PASSWORD'] = required_env('BOOTSTRAP_ADMIN_PASSWORD')
        app.config['SQLALCHEMY_DATABASE_URI'] = required_env('DATABASE_URL')
        app.config['RATELIMIT_STORAGE_URI'] = required_env('RATELIMIT_STORAGE_URI')


def init_app(app):
    """Apply configuration that depends on the selected environment."""
    initializer = getattr(current_config, 'init_app', None)
    if initializer:
        initializer(app)


# Configuration dictionary for easy access
config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}

# Get current environment
FLASK_ENV = os.environ.get('FLASK_ENV', 'development')
current_config = config.get(FLASK_ENV, DevelopmentConfig)

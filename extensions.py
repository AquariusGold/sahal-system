"""Shared Flask extension instances (avoids circular imports)"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

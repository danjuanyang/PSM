# PSM/app/email/__init__.py
from flask import Blueprint

email_bp = Blueprint('email', __name__)

from . import routes
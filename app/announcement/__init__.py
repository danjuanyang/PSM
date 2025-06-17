from flask import Blueprint

announcement_bp = Blueprint('announcement', __name__)

from . import routes

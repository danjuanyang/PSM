# PSM/app/knowledge_base/__init__.py
from flask import Blueprint

kb_bp = Blueprint('knowledge_base', __name__)

from . import routes

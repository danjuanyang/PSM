from . import alerts_bp

@alerts_bp.route('/')
def index():
    return "Alerts"

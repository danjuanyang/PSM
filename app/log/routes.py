from . import log_bp

@log_bp.route('/')
def index():
    return 'This is the log blueprint.'

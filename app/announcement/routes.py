from . import announcement_bp

@announcement_bp.route('/')
def index():
    return 'This is the announcement blueprint.'

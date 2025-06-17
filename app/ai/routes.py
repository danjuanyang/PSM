from . import ai_bp

@ai_bp.route('/')
def index():
    return 'This is the ai blueprint.'

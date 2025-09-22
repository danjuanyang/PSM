
# PSM/app/backup/routes.py
import os
from flask import jsonify, send_from_directory, current_app
from . import backup_bp
from .service import BackupService
from ..decorators import permission_required, log_activity

backup_service = BackupService()

@backup_bp.route('/manual-backup', methods=['POST'])
@permission_required('manage_system_settings')
@log_activity('手动系统备份', action_detail_template='创建并下载了手动系统备份')
def manual_backup():
    """
    触发手动系统备份并提供下载。
    """
    try:
        archive_path = backup_service.create_backup_archive()
        directory = os.path.dirname(archive_path)
        filename = os.path.basename(archive_path)
        
        return send_from_directory(
            directory=directory,
            path=filename,
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        current_app.logger.error(f"手动备份失败: {e}", exc_info=True)
        return jsonify({'error': '创建备份文件时出错', 'details': str(e)}), 500

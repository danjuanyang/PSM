# PSM/app/backup/service.py
import os
import shutil
import datetime
import logging
import zipfile
from flask import current_app

logger = logging.getLogger(__name__)

class BackupService:
    """处理备份创建和管理的业务逻辑"""

    def create_backup_archive(self):
        """
        创建系统备份 (数据库 + 上传的文件) 并将其存储在备份目录中。
        返回创建的备份文件的路径。
        """
        upload_folder = current_app.config.get('UPLOAD_FOLDER')
        data_folder = current_app.config.get('DATA_FOLDER')
        backup_folder = current_app.config.get('BACKUP_FOLDER')

        if not all([upload_folder, data_folder, backup_folder]):
            raise ValueError("备份路径未正确配置 (UPLOAD_FOLDER, DATA_FOLDER, or BACKUP_FOLDER)")

        # 备份文件将存储在独立的备份目录下，并按日期分子目录
        backup_date_dir = os.path.join(backup_folder, datetime.datetime.now().strftime("%Y-%m-%d"))
        os.makedirs(backup_date_dir, exist_ok=True)

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename_base = f'psm_backup_{timestamp}'
        archive_path_base = os.path.join(backup_date_dir, backup_filename_base)

        try:
            logger.info(f"Starting backup creation: {archive_path_base}.zip")
            
            # 1. 压缩 data 目录
            archive_path = shutil.make_archive(
                base_name=archive_path_base,
                format='zip',
                root_dir=data_folder,
                base_dir='.',
                logger=logger
            )

            # 2. 将 uploads 目录添加到同一个压缩包
            with zipfile.ZipFile(archive_path, 'a') as zf:
                for root, dirs, files in os.walk(upload_folder):
                    # 排除任何可能存在于uploads目录下的备份文件，虽然现在分开了，但这是个好习惯
                    if backup_folder and os.path.commonpath([root, backup_folder]) == backup_folder:
                        continue
                    
                    for file in files:
                        file_path = os.path.join(root, file)
                        # 计算文件在压缩包内的相对路径，确保所有文件都在 'uploads/' 文件夹下
                        arcname = os.path.join('uploads', os.path.relpath(file_path, upload_folder))
                        zf.write(file_path, arcname)
            
            logger.info(f"Successfully created backup archive: {archive_path}")
            return archive_path

        except Exception as e:
            logger.error(f"创建备份压缩包时出错: {e}", exc_info=True)
            # 清理失败时可能产生的临时文件
            if os.path.exists(f"{archive_path_base}.zip"):
                os.remove(f"{archive_path_base}.zip")
            raise
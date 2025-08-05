# PSM/app/files/cleanup_scheduler.py

import os
import shutil
import time
from datetime import datetime, timedelta
from flask import current_app
import threading

class TempFileCleanupScheduler:
    """临时文件清理调度器"""
    
    def __init__(self, app=None):
        self.app = app
        self.cleanup_thread = None
        self.running = False
        self.cleanup_interval = 7 * 24 * 60 * 60  # 7天（秒）
        
    def init_app(self, app):
        """初始化应用"""
        self.app = app
        
    def start_cleanup_scheduler(self):
        """启动清理调度器"""
        if not self.running:
            self.running = True
            self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
            self.cleanup_thread.start()
            current_app.logger.info("临时文件清理调度器已启动")
            
    def stop_cleanup_scheduler(self):
        """停止清理调度器"""
        self.running = False
        if self.cleanup_thread:
            self.cleanup_thread.join()
            current_app.logger.info("临时文件清理调度器已停止")
            
    def _cleanup_loop(self):
        """清理循环"""
        while self.running:
            try:
                with self.app.app_context():
                    self.cleanup_old_temp_files()
                # 等待下次清理
                time.sleep(self.cleanup_interval)
            except Exception as e:
                current_app.logger.error(f"清理调度器出错: {e}")
                time.sleep(300)  # 出错后等待5分钟再重试
                
    def cleanup_old_temp_files(self):
        """清理过期的临时文件"""
        try:
            temp_dir = current_app.config.get('TEMP_DIR')
            if not temp_dir or not os.path.exists(temp_dir):
                return
                
            current_app.logger.info(f"开始清理临时文件目录: {temp_dir}")
            
            # 获取7天前的时间
            cutoff_time = datetime.now() - timedelta(days=7)
            cutoff_timestamp = cutoff_time.timestamp()
            
            cleaned_count = 0
            cleaned_size = 0
            
            # 遍历temp目录下的所有子目录
            for item_name in os.listdir(temp_dir):
                item_path = os.path.join(temp_dir, item_name)
                
                if os.path.isdir(item_path):
                    try:
                        # 检查目录的修改时间
                        dir_mtime = os.path.getmtime(item_path)
                        
                        if dir_mtime < cutoff_timestamp:
                            # 计算目录大小
                            dir_size = self._get_directory_size(item_path)
                            
                            # 删除过期目录
                            shutil.rmtree(item_path)
                            cleaned_count += 1
                            cleaned_size += dir_size
                            
                            current_app.logger.info(f"已清理过期临时目录: {item_path}")
                            
                    except Exception as e:
                        current_app.logger.error(f"清理目录 {item_path} 时出错: {e}")
                        
            if cleaned_count > 0:
                current_app.logger.info(
                    f"临时文件清理完成: 清理了 {cleaned_count} 个目录，"
                    f"释放空间 {self._format_size(cleaned_size)}"
                )
            else:
                current_app.logger.info("没有找到需要清理的过期临时文件")
                
        except Exception as e:
            current_app.logger.error(f"清理临时文件失败: {e}")
            
    def _get_directory_size(self, directory):
        """获取目录大小"""
        total_size = 0
        try:
            for dirpath, dirnames, filenames in os.walk(directory):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    try:
                        total_size += os.path.getsize(filepath)
                    except (OSError, IOError):
                        continue
        except Exception:
            pass
        return total_size
        
    def _format_size(self, size_bytes):
        """格式化文件大小"""
        if size_bytes == 0:
            return "0B"
        
        size_names = ["B", "KB", "MB", "GB", "TB"]
        import math
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_names[i]}"

# 创建全局清理调度器实例
cleanup_scheduler = TempFileCleanupScheduler()
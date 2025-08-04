# PSM/app/files/merge_tasks.py
import os
import uuid
import time
import shutil
import tempfile
from datetime import datetime
from flask import current_app
import PyPDF2
from PIL import Image
import fitz  # PyMuPDF
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from celery_app import celery
from .. import db
from ..models import FileMergeTask, FileMergeTaskStatusEnum, ProjectFile, Project

# 简化版本：直接执行任务而不使用Celery（可以后续扩展为异步）
class MockCeleryTask:
    """模拟Celery任务，实际上同步执行"""
    def __init__(self, func):
        self.func = func
        self.id = str(uuid.uuid4())
        
    def delay(self, *args, **kwargs):
        """模拟异步执行，实际上同步执行"""
        try:
            result = self.func(*args, **kwargs)
            return MockCeleryResult(self.id, result, 'SUCCESS')
        except Exception as e:
            return MockCeleryResult(self.id, None, 'FAILURE', str(e))

class MockCeleryResult:
    """模拟Celery结果"""
    def __init__(self, task_id, result, status, error=None):
        self.id = task_id
        self.result = result
        self.status = status
        self.error = error


def update_task_progress(task_id, progress, status=None, status_message=None, error_message=None):
    """更新任务进度"""
    with current_app.app_context():
        merge_task = FileMergeTask.query.filter_by(task_id=task_id).first()
        if merge_task:
            merge_task.progress = progress
            if status:
                merge_task.status = status
            if status_message:
                merge_task.status_message = status_message
            if error_message:
                merge_task.error_message = error_message
            merge_task.updated_at = datetime.now()
            
            if status in [FileMergeTaskStatusEnum.COMPLETED, FileMergeTaskStatusEnum.FAILED]:
                merge_task.completed_at = datetime.now()
                
            db.session.commit()


def get_file_pages_count(file_path):
    """获取PDF文件页数"""
    try:
        with open(file_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            return len(pdf_reader.pages)
    except Exception as e:
        current_app.logger.error(f"获取PDF页数失败: {e}")
        return 0


def create_cover_page(title, subtitle="", author="", date=""):
    """创建封面页PDF"""
    try:
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        doc = SimpleDocTemplate(temp_file.name, pagesize=A4)
        
        # 样式
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Title'],
            fontSize=24,
            spaceAfter=30,
            alignment=1  # 居中
        )
        
        subtitle_style = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Normal'],
            fontSize=16,
            spaceAfter=20,
            alignment=1
        )
        
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=12,
            spaceAfter=12,
            alignment=1
        )
        
        # 内容
        story = []
        story.append(Spacer(1, 2*inch))
        story.append(Paragraph(title, title_style))
        
        if subtitle:
            story.append(Paragraph(subtitle, subtitle_style))
            
        story.append(Spacer(1, 1*inch))
        
        if author:
            story.append(Paragraph(f"作者: {author}", normal_style))
            
        if date:
            story.append(Paragraph(f"日期: {date}", normal_style))
        
        doc.build(story)
        return temp_file.name
        
    except Exception as e:
        current_app.logger.error(f"创建封面页失败: {e}")
        return None


def create_toc_page(file_list):
    """创建目录页PDF"""
    try:
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        doc = SimpleDocTemplate(temp_file.name, pagesize=A4)
        
        # 样式
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'TOCTitle',
            parent=styles['Title'],
            fontSize=18,
            spaceAfter=20,
            alignment=1
        )
        
        # 内容
        story = []
        story.append(Paragraph("目录", title_style))
        story.append(Spacer(1, 0.3*inch))
        
        # 目录表格
        toc_data = [['序号', '文件名', '页码']]
        current_page = 1
        
        # 如果有封面页，页码从2开始
        if any(file_info.get('cover_page') for file_info in file_list):
            current_page = 2
            
        # 目录页本身占1页
        current_page += 1
        
        for i, file_info in enumerate(file_list, 1):
            file_name = file_info.get('original_name', f'文件{i}')
            pages_count = file_info.get('pages_count', 1)
            toc_data.append([str(i), file_name, str(current_page)])
            current_page += pages_count
        
        toc_table = Table(toc_data, colWidths=[1*inch, 4*inch, 1*inch])
        toc_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        story.append(toc_table)
        doc.build(story)
        return temp_file.name
        
    except Exception as e:
        current_app.logger.error(f"创建目录页失败: {e}")
        return None


def merge_pdfs(file_paths_list, output_path, cover_path=None, toc_path=None, pages_to_delete=None):
    """合并PDF文件"""
    try:
        pdf_writer = PyPDF2.PdfWriter()
        
        # 添加封面页
        if cover_path and os.path.exists(cover_path):
            with open(cover_path, 'rb') as cover_file:
                cover_reader = PyPDF2.PdfReader(cover_file)
                for page in cover_reader.pages:
                    pdf_writer.add_page(page)
        
        # 添加目录页
        if toc_path and os.path.exists(toc_path):
            with open(toc_path, 'rb') as toc_file:
                toc_reader = PyPDF2.PdfReader(toc_file)
                for page in toc_reader.pages:
                    pdf_writer.add_page(page)
        
        # 添加文件内容
        pages_to_delete = pages_to_delete or []
        current_page_index = 0
        
        # 如果有封面页和目录页，调整页面索引
        if cover_path:
            current_page_index += 1
        if toc_path:
            current_page_index += 1
        
        for file_path in file_paths_list:
            if os.path.exists(file_path):
                with open(file_path, 'rb') as pdf_file:
                    pdf_reader = PyPDF2.PdfReader(pdf_file)
                    for page_num, page in enumerate(pdf_reader.pages):
                        if current_page_index not in pages_to_delete:
                            pdf_writer.add_page(page)
                        current_page_index += 1
        
        # 写入输出文件
        with open(output_path, 'wb') as output_file:
            pdf_writer.write(output_file)
            
        return True
        
    except Exception as e:
        current_app.logger.error(f"合并PDF失败: {e}")
        return False


def get_ordered_pdf_files(project_id, selected_file_ids):
    """获取按照选择顺序排序的PDF文件列表"""
    if not selected_file_ids:
        # 如果没有指定文件，按默认顺序获取项目下所有PDF文件
        files = ProjectFile.query.filter(
            ProjectFile.project_id == project_id,
            ProjectFile.file_type == 'pdf'
        ).order_by(ProjectFile.upload_date.asc()).all()
    else:
        # 按照选择的顺序获取文件 - 关键：保持用户选择的顺序
        files = []
        for file_id in selected_file_ids:
            file_obj = ProjectFile.query.filter(
                ProjectFile.id == file_id,
                ProjectFile.project_id == project_id,
                ProjectFile.file_type == 'pdf'
            ).first()
            if file_obj and os.path.exists(file_obj.file_path):
                files.append(file_obj)
    
    return files


def convert_pdf_to_images_with_order(pdf_path, output_dir, dpi=150):
    """将PDF转换为图片，保持页面顺序信息 (使用 PyMuPDF)"""
    try:
        doc = fitz.open(pdf_path)
        image_paths = []
        
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi)
            image_filename = f"page_{i+1}.png"
            image_path = os.path.join(output_dir, image_filename)
            pix.save(image_path)
            image_paths.append({
                'page_number': i + 1,
                'page_index': i,  # 0-based索引，用于删除操作
                'filename': image_filename,
                'url': f"/api/files/merge/temp_preview_image/{os.path.basename(output_dir)}/{image_filename}"
            })
        
        doc.close()
        return image_paths
        
    except Exception as e:
        current_app.logger.error(f"PDF转图片失败 (PyMuPDF): {e}")
        return []


def convert_pdf_to_images(pdf_path, output_dir, dpi=150):
    """将PDF转换为图片 (使用 PyMuPDF)"""
    try:
        doc = fitz.open(pdf_path)
        image_paths = []
        
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi)
            image_filename = f"page_{i+1}.png"
            image_path = os.path.join(output_dir, image_filename)
            pix.save(image_path)
            image_paths.append({
                'page_number': i + 1,
                'filename': image_filename,
                'url': f"/api/files/temp_preview_image/{os.path.basename(output_dir)}/{image_filename}"
            })
            
        doc.close()
        return image_paths
        
    except Exception as e:
        current_app.logger.error(f"PDF转图片失败 (PyMuPDF): {e}")
        return []


@celery.task(bind=True)
def generate_preview_task(self, task_id, project_id, selected_file_ids, merge_config):
    """生成预览的Celery任务 - 优化版本"""
    try:
        with current_app.app_context():
            update_task_progress(task_id, 5, FileMergeTaskStatusEnum.GENERATING_PREVIEW, "开始生成预览...")
            
            # 获取项目
            project = Project.query.get(project_id)
            if not project:
                raise Exception("项目不存在")
            
            # 按选择顺序获取PDF文件
            files = get_ordered_pdf_files(project_id, selected_file_ids)
            
            if not files:
                raise Exception("没有找到可合并的PDF文件")
            
            current_app.logger.info(f"找到 {len(files)} 个PDF文件，按以下顺序合并：")
            for i, file_obj in enumerate(files):
                current_app.logger.info(f"  {i+1}. {file_obj.original_name}")
            
            update_task_progress(task_id, 15, status_message="创建临时目录...")
            
            # 创建临时目录
            temp_dir = tempfile.mkdtemp()
            preview_session_id = str(uuid.uuid4())
            image_output_dir = os.path.join(temp_dir, preview_session_id)
            os.makedirs(image_output_dir, exist_ok=True)
            
            update_task_progress(task_id, 25, status_message="创建封面和目录...")
            
            # 创建封面页
            cover_path = None
            cover_options = merge_config.get('coverPage', {})
            if cover_options.get('enabled', False):
                cover_path = create_cover_page(
                    title=cover_options.get('title', project.name),
                    subtitle=cover_options.get('subtitle', ''),
                    author=cover_options.get('author', ''),
                    date=cover_options.get('date', datetime.now().strftime('%Y-%m-%d'))
                )
            
            # 创建目录页
            toc_path = None
            toc_options = merge_config.get('toc', {})
            if toc_options.get('enabled', False):
                # 根据文件顺序创建目录
                file_info_list = []
                for file_obj in files:
                    pages_count = get_file_pages_count(file_obj.file_path)
                    file_info_list.append({
                        'original_name': file_obj.original_name,
                        'pages_count': pages_count
                    })
                toc_path = create_toc_page(file_info_list)
            
            update_task_progress(task_id, 40, status_message="合并PDF文件...")
            
            # 合并PDF - 关键：按照文件选择顺序合并
            merged_pdf_path = os.path.join(temp_dir, 'merged_preview.pdf')
            file_paths_in_order = [file_obj.file_path for file_obj in files]
            
            success = merge_pdfs(file_paths_in_order, merged_pdf_path, cover_path, toc_path)
            if not success:
                raise Exception("PDF合并失败")
            
            update_task_progress(task_id, 60, status_message="生成预览图片...")
            
            # 转换为图片 - 保持页面的原始索引
            image_info = convert_pdf_to_images_with_order(merged_pdf_path, image_output_dir)
            if not image_info:
                raise Exception("生成预览图片失败")
            
            update_task_progress(task_id, 80, status_message="保存预览信息...")
            
            # 更新任务信息
            merge_task = FileMergeTask.query.filter_by(task_id=task_id).first()
            if merge_task:
                merge_task.preview_session_id = preview_session_id
                merge_task.preview_image_urls = image_info
                
                # 保存文件信息，用于前端显示文件源信息
                file_source_info = []
                page_index = 0
                
                # 如果有封面页，记录封面页信息
                if cover_path:
                    file_source_info.append({
                        'page_range': [page_index],
                        'source_type': 'cover',
                        'source_name': '封面页'
                    })
                    page_index += 1
                
                # 如果有目录页，记录目录页信息  
                if toc_path:
                    file_source_info.append({
                        'page_range': [page_index],
                        'source_type': 'toc',
                        'source_name': '目录页'
                    })
                    page_index += 1
                
                # 记录每个文件的页面范围
                for file_obj in files:
                    pages_count = get_file_pages_count(file_obj.file_path)
                    page_range = list(range(page_index, page_index + pages_count))
                    file_source_info.append({
                        'page_range': page_range,
                        'source_type': 'file',
                        'source_name': file_obj.original_name,
                        'file_id': file_obj.id
                    })
                    page_index += pages_count
                
                # 存储文件源信息到数据库
                merge_task.merge_config = {
                    **merge_config,
                    'file_source_info': file_source_info
                }
                
                db.session.commit()
            
            # 清理临时文件（保留图片目录给前端访问）
            if cover_path and os.path.exists(cover_path):
                os.unlink(cover_path)
            if toc_path and os.path.exists(toc_path):
                os.unlink(toc_path)
            if os.path.exists(merged_pdf_path):
                os.unlink(merged_pdf_path)
            
            update_task_progress(task_id, 100, FileMergeTaskStatusEnum.PREVIEW_READY, "预览生成完成")
            
            return {
                'preview_session_id': preview_session_id,
                'image_info': image_info,
                'temp_dir': temp_dir
            }
            
    except Exception as e:
        current_app.logger.error(f"生成预览任务失败: {e}")
        update_task_progress(task_id, 100, FileMergeTaskStatusEnum.FAILED, "生成预览失败", str(e))
        raise


@celery.task(bind=True)
def generate_final_pdf_task(self, task_id, project_id, selected_file_ids, merge_config, pages_to_delete_indices):
    """生成最终PDF的Celery任务 - 优化版本"""
    try:
        with current_app.app_context():
            update_task_progress(task_id, 5, FileMergeTaskStatusEnum.GENERATING_FINAL, "开始生成最终PDF...")
            
            # 获取项目
            project = Project.query.get(project_id)
            if not project:
                raise Exception("项目不存在")
            
            # 按选择顺序获取PDF文件
            files = get_ordered_pdf_files(project_id, selected_file_ids)
            
            if not files:
                raise Exception("没有找到可合并的PDF文件")
            
            current_app.logger.info(f"最终合并 {len(files)} 个PDF文件，按以下顺序：")
            for i, file_obj in enumerate(files):
                current_app.logger.info(f"  {i+1}. {file_obj.original_name}")
            
            update_task_progress(task_id, 20, status_message="准备合并文件...")
            
            # 创建封面页
            cover_path = None
            cover_options = merge_config.get('coverPage', {})
            if cover_options.get('enabled', False):
                cover_path = create_cover_page(
                    title=cover_options.get('title', project.name),
                    subtitle=cover_options.get('subtitle', ''),
                    author=cover_options.get('author', ''),
                    date=cover_options.get('date', datetime.now().strftime('%Y-%m-%d'))
                )
            
            # 创建目录页
            toc_path = None
            toc_options = merge_config.get('toc', {})
            if toc_options.get('enabled', False):
                file_info_list = []
                for file_obj in files:
                    pages_count = get_file_pages_count(file_obj.file_path)
                    file_info_list.append({
                        'original_name': file_obj.original_name,
                        'pages_count': pages_count
                    })
                toc_path = create_toc_page(file_info_list)
            
            update_task_progress(task_id, 40, status_message="合并PDF文件...")
            
            # 创建输出目录
            output_dir = os.path.join(current_app.config.get('UPLOAD_FOLDER', '/tmp'), 'merged_files')
            os.makedirs(output_dir, exist_ok=True)
            
            # 生成最终文件路径
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            final_filename = f"{project.name}_merged_{timestamp}.pdf"
            temp_merged_path = os.path.join(output_dir, f"temp_{final_filename}")
            
            # 按照文件选择顺序合并PDF
            file_paths_in_order = [file_obj.file_path for file_obj in files]
            success = merge_pdfs(file_paths_in_order, temp_merged_path, cover_path, toc_path, pages_to_delete_indices)
            
            if not success:
                raise Exception("PDF合并失败")
            
            update_task_progress(task_id, 80, status_message="优化最终文件...")
            
            # 最终文件路径
            final_file_path = os.path.join(output_dir, final_filename)
            
            # 如果有页面删除操作，处理页面删除
            if pages_to_delete_indices and len(pages_to_delete_indices) > 0:
                try:
                    import PyPDF2
                    reader = PyPDF2.PdfReader(temp_merged_path)
                    writer = PyPDF2.PdfWriter()
                    
                    # 添加未被删除的页面
                    for i, page in enumerate(reader.pages):
                        if i not in pages_to_delete_indices:
                            writer.add_page(page)
                    
                    # 写入最终文件
                    with open(final_file_path, 'wb') as output_file:
                        writer.write(output_file)
                        
                    current_app.logger.info(f"已删除 {len(pages_to_delete_indices)} 个页面")
                except Exception as e:
                    current_app.logger.error(f"删除页面时出错: {e}")
                    # 如果删除页面失败，使用原文件
                    shutil.move(temp_merged_path, final_file_path)
            else:
                # 没有页面删除，直接移动文件
                shutil.move(temp_merged_path, final_file_path)
            
            update_task_progress(task_id, 90, status_message="保存最终文件...")
            
            # 更新任务信息
            merge_task = FileMergeTask.query.filter_by(task_id=task_id).first()
            if merge_task:
                merge_task.final_file_path = final_file_path
                merge_task.final_file_name = final_filename
                db.session.commit()
            
            # 清理临时文件
            if cover_path and os.path.exists(cover_path):
                os.unlink(cover_path)
            if toc_path and os.path.exists(toc_path):
                os.unlink(toc_path)
            if os.path.exists(temp_merged_path):
                os.unlink(temp_merged_path)
            
            update_task_progress(task_id, 100, FileMergeTaskStatusEnum.COMPLETED, "最终PDF生成完成")
            
            return {
                'final_file_path': final_file_path,
                'final_filename': final_filename
            }
            
    except Exception as e:
        current_app.logger.error(f"生成最终PDF任务失败: {e}")
        update_task_progress(task_id, 100, FileMergeTaskStatusEnum.FAILED, "生成最终PDF失败", str(e))
        raise


def cleanup_temp_files(temp_dir):
    """清理临时文件"""
    try:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            current_app.logger.info(f"清理临时目录: {temp_dir}")
    except Exception as e:
        current_app.logger.error(f"清理临时文件失败: {e}")
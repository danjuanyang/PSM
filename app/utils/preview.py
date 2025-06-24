import os
from flask import send_file, Response, jsonify, current_app
import docx

def _get_file_extension(filepath):
    """获取文件的小写扩展名"""
    return os.path.splitext(filepath)[1].lower()

def _preview_image_or_pdf(file_path):
    """直接发送图片或PDF文件，浏览器会自动处理预览"""
    #使用 'inline' 建议浏览器显示它，而不是下载
    return send_file(file_path, as_attachment=False)

def _preview_text(file_path):
    """读取文本文件内容并以HTML <pre> 标签格式返回，以保留格式"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        # 使用<pre>标签保留格式，如空格和换行符
        html_content = f"<html><head><meta charset='UTF-8'><title>Preview</title></head><body><pre style='word-wrap: break-word; white-space: pre-wrap;'>{content}</pre></body></html>"
        return Response(html_content, mimetype='text/html')
    except Exception as e:
        current_app.logger.error(f"预览文本文件时出错: {file_path}: {e}")
        return jsonify({"error": "无法预览此文本文件，可能存在编码问题。"}), 500

def _preview_docx(file_path):
    """从.docx文件中提取文本并以简单的HTML格式返回"""
    try:
        doc = docx.Document(file_path)
        full_text = []
        for para in doc.paragraphs:
            full_text.append(para.text)
        # <p> 对段落使用标签以使其更具可读性
        html_content = f"<html><head><meta charset='UTF-8'><title>Preview</title></head><body>{''.join(f'<p>{p}</p>' for p in full_text)}</body></html>"
        return Response(html_content, mimetype='text/html')
    except Exception as e:
        current_app.logger.error(f"Error previewing docx file {file_path}: {e}")
        return jsonify({"error": "无法解析此 .docx 文件。"}), 500

def generate_file_preview(file_path):
    """
    根据文件路径和类型，调用相应的处理函数来生成文件预览的Flask响应。
    """
    if not os.path.exists(file_path):
        return jsonify({"error": "文件在服务器上未找到"}), 404

    ext = _get_file_extension(file_path)

    # 将扩展映射到预览函数
    preview_map = {
        '.png': _preview_image_or_pdf,
        '.jpg': _preview_image_or_pdf,
        '.jpeg': _preview_image_or_pdf,
        '.gif': _preview_image_or_pdf,
        '.pdf': _preview_image_or_pdf,
        '.txt': _preview_text,
        '.md': _preview_text,
        '.py': _preview_text,
        '.js': _preview_text,
        '.json': _preview_text,
        '.docx': _preview_docx,
    }

    preview_function = preview_map.get(ext)
    if preview_function:
        return preview_function(file_path)
    else:
        #对于不支持的格式，请返回一条明文邮件
        return jsonify({"message": "此文件类型不支持在线预览，请下载后查看。", "supported": False}), 415


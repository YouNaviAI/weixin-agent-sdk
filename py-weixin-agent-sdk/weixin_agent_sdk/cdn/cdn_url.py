"""
微信 CDN 上传/下载 URL 构建工具。
"""

from __future__ import annotations

from urllib.parse import quote


def build_cdn_download_url(encrypted_query_param: str, cdn_base_url: str) -> str:
    """根据 encrypt_query_param 构建 CDN 下载 URL。"""
    return f"{cdn_base_url}/download?encrypted_query_param={quote(encrypted_query_param)}"


def build_cdn_upload_url(cdn_base_url: str, upload_param: str, filekey: str) -> str:
    """根据 upload_param 和 filekey 构建 CDN 上传 URL（旧格式）。"""
    return (
        f"{cdn_base_url}/upload"
        f"?encrypted_query_param={quote(upload_param)}"
        f"&filekey={quote(filekey)}"
    )

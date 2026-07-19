"""
MinIO 对象存储客户端封装
用于存储检测图像、训练模型等文件
"""
import io
import os
import uuid

from minio import Minio
from minio.error import S3Error

from app.config.settings import settings


class MinIOClient:
    """MinIO 客户端封装"""

    def __init__(self, ensure_bucket: bool = True):
        self.client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        self.bucket_name = settings.MINIO_BUCKET
        if ensure_bucket:
            self._ensure_bucket()

    def _ensure_bucket(self):
        """确保存储桶存在，不存在则创建"""
        try:
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
        except S3Error as e:
            print(f"MinIO bucket 初始化警告: {e}")

    def upload_file(
        self,
        object_name: str,
        file_path: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """
        上传本地文件到 MinIO

        Args:
            object_name: MinIO 中的对象名称（路径）
            file_path: 本地文件路径
            content_type: 对象 MIME 类型，确保历史中的图片/视频可直接预览

        Returns:
            预签名 URL
        """
        self.client.fput_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
            file_path=file_path,
            content_type=content_type,
        )
        return self.get_presigned_url(object_name)

    def upload_bytes(
        self, object_name: str, data: bytes, content_type: str = "image/jpeg"
    ) -> str:
        """
        上传字节数据到 MinIO

        Args:
            object_name: MinIO 中的对象名称
            data: 字节数据
            content_type: MIME 类型

        Returns:
            预签名 URL
        """
        self.client.put_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
            data=io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        return self.get_presigned_url(object_name)

    def download_file(self, object_name: str, file_path: str) -> str:
        """把 MinIO 对象原子下载到指定本地路径。"""
        target_path = os.path.abspath(file_path)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        temporary_path = f"{target_path}.{uuid.uuid4().hex}.part"
        try:
            self.client.fget_object(
                bucket_name=self.bucket_name,
                object_name=object_name,
                file_path=temporary_path,
            )
            os.replace(temporary_path, target_path)
        finally:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
        return target_path

    def get_presigned_url(self, object_name: str) -> str:
        """获取对象的预签名访问 URL（有效期 7 天）"""
        from datetime import timedelta

        url = self.client.presigned_get_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
            expires=timedelta(days=7),
        )
        return url

    def delete_file(self, object_name: str):
        """删除 MinIO 中的文件"""
        self.client.remove_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
        )

    def object_name_from_url(self, url: str) -> str | None:
        """
        从预签名 URL 反解出永久对象名（object_name）。

        预签名 URL 形如 http://host/bucket/detections/1/a.jpg?X-Amz-...
        取 bucket 之后、查询串之前的路径部分。用于把易过期的 URL 归一化为
        可长期存库的对象标识。

        Returns:
            object_name；无法解析时返回 None。
        """
        if not url:
            return None
        from urllib.parse import unquote, urlparse

        path = urlparse(url).path.lstrip("/")
        prefix = f"{self.bucket_name}/"
        if not path.startswith(prefix):
            return None
        object_name = path[len(prefix):]
        return unquote(object_name) or None

    def presign_from_url_or_name(self, value: str) -> str | None:
        """
        输入既可能是 object_name、也可能是历史遗留的预签名 URL，
        统一换签为一个新的短期访问 URL。对象不存在或换签失败时返回 None。
        """
        if not value:
            return None
        object_name = (
            self.object_name_from_url(value)
            if value.startswith(("http://", "https://"))
            else value
        )
        if not object_name:
            return None
        try:
            return self.get_presigned_url(object_name)
        except Exception:
            return None

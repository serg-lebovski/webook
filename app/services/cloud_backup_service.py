"""Загрузка ZIP-бэкапа пользователя в S3-совместимое хранилище (AWS S3, MinIO,
Backblaze B2 и т.п.), настраиваемое администратором через SiteSettings."""
import json
import os
from datetime import datetime

from sqlalchemy.orm import Session

from app.services.settings_service import get_setting, set_setting


def is_configured(db: Session) -> bool:
    return bool(get_setting(db, "s3_bucket", "") and get_setting(db, "s3_access_key", ""))


def _client(db: Session):
    import boto3
    endpoint = get_setting(db, "s3_endpoint", "").strip()
    access_key = get_setting(db, "s3_access_key", "").strip()
    secret_key = get_setting(db, "s3_secret_key", "").strip()
    region = get_setting(db, "s3_region", "us-east-1").strip() or "us-east-1"
    if not (access_key and secret_key):
        return None
    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )


def last_backup_info(db: Session, user_id: int) -> dict | None:
    raw = get_setting(db, f"cloud_backup_last_{user_id}", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def upload_backup(db: Session, user, zip_path: str) -> tuple[bool, str]:
    """Возвращает (успех, сообщение/ключ объекта)."""
    bucket = get_setting(db, "s3_bucket", "").strip()
    try:
        client = _client(db)
    except Exception as e:
        return False, f"Облачное хранилище недоступно: {e}"
    if not client or not bucket:
        return False, "Облачное хранилище не настроено администратором"
    key = f"{user.username}/webook_{datetime.utcnow().strftime('%Y-%m-%d_%H%M%S')}.zip"
    try:
        client.upload_file(zip_path, bucket, key)
    except Exception as e:
        return False, f"Ошибка загрузки: {e}"
    size = os.path.getsize(zip_path)
    set_setting(db, f"cloud_backup_last_{user.id}", json.dumps({
        "at": datetime.utcnow().isoformat(), "key": key, "size": size, "bucket": bucket,
    }))
    return True, key

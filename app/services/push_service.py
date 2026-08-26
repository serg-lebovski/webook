"""Web Push (браузерные уведомления вне открытой вкладки). VAPID-ключи
генерируются один раз при первом использовании и хранятся в SiteSettings —
как s3_* в cloud_backup_service, без ручной настройки .env."""
import base64
import json

from sqlalchemy.orm import Session

from app.models.push_subscription import PushSubscription
from app.services.settings_service import get_setting, set_setting

CONTACT_EMAIL = "admin@webook.local"


def _vapid_keys(db: Session) -> tuple[str, str]:
    """Возвращает (public_b64url, private_pem), генерируя пару при первом обращении."""
    pub = get_setting(db, "vapid_public_key", "")
    priv = get_setting(db, "vapid_private_key", "")
    if pub and priv:
        return pub, priv

    from py_vapid import Vapid02
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    v = Vapid02()
    v.generate_keys()
    priv_pem = v.private_pem().decode("utf-8")
    raw_pub = v.public_key.public_bytes(
        encoding=Encoding.X962, format=PublicFormat.UncompressedPoint,
    )
    pub_b64 = base64.urlsafe_b64encode(raw_pub).decode("utf-8").rstrip("=")
    set_setting(db, "vapid_public_key", pub_b64)
    set_setting(db, "vapid_private_key", priv_pem)
    return pub_b64, priv_pem


def public_key(db: Session) -> str:
    pub, _ = _vapid_keys(db)
    return pub


def subscribe(db: Session, user_id: int, endpoint: str, p256dh: str, auth: str) -> None:
    existing = db.query(PushSubscription).filter_by(endpoint=endpoint).first()
    if existing:
        existing.user_id = user_id
        existing.p256dh = p256dh
        existing.auth = auth
    else:
        db.add(PushSubscription(user_id=user_id, endpoint=endpoint, p256dh=p256dh, auth=auth))
    db.commit()


def unsubscribe(db: Session, endpoint: str) -> None:
    db.query(PushSubscription).filter_by(endpoint=endpoint).delete()
    db.commit()


def send_to_user(db: Session, user_id: int, title: str, body: str, url: str = "/dashboard") -> int:
    """Отправляет уведомление во все подписки пользователя. Возвращает число успешных отправок.
    Протухшие подписки (410/404) удаляются молча."""
    from pywebpush import webpush, WebPushException

    subs = db.query(PushSubscription).filter_by(user_id=user_id).all()
    if not subs:
        return 0
    _, priv_pem = _vapid_keys(db)
    payload = json.dumps({"title": title, "body": body, "url": url})
    sent = 0
    for sub in subs:
        subscription_info = {
            "endpoint": sub.endpoint,
            "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=priv_pem,
                vapid_claims={"sub": f"mailto:{CONTACT_EMAIL}"},
            )
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                db.delete(sub)
                db.commit()
        except Exception:
            pass
    return sent

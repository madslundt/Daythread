"""APNs delivery runs independently of HTTP requests and survives client disconnects."""

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from uuid import UUID

import httpx
import jwt

from .notification_store import public_notification
from .settings import Settings
from .store import Store

logger = logging.getLogger(__name__)


class APNsSender:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(http2=True, timeout=15, follow_redirects=False)
        self._jwt = ""
        self._jwt_at = 0.0
        self._credentials = None

    @property
    def configured(self) -> bool:
        return bool(self.settings.apns_team_id and self.settings.apns_key_id
                    and self.settings.apns_private_key.get_secret_value()
                    and self.settings.apns_topic)

    def authorization(self) -> str:
        credentials = (self.settings.apns_team_id, self.settings.apns_key_id,
                       self.settings.apns_private_key.get_secret_value())
        if credentials != self._credentials or time.time() - self._jwt_at >= 3000:
            token = jwt.encode(
                {"iss": credentials[0], "iat": int(time.time())}, credentials[2],
                algorithm="ES256", headers={"kid": credentials[1]},
            )
            self._jwt, self._jwt_at, self._credentials = token, time.time(), credentials
        return self._jwt

    async def send(self, delivery: dict, profile_id: str) -> tuple[int, str]:
        event = public_notification(delivery)
        collapse_id = event["id"]
        if event["kind"] == "wearable_action":
            try:
                # Matches the phone's immediate local confirmation. APNs uses
                # its collapse ID as the delivered UNNotificationRequest ID.
                collapse_id = "wearable-action:" + str(UUID(event["target"]["job_id"]))
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
        payload = {
            "aps": {"alert": {"title": event["title"], "body": event["body"]},
                    "sound": "default", "thread-id": f"daythread-{profile_id}"},
            "notification": event, "profile_id": profile_id, "device_id": delivery["device_id"],
        }
        # APNs caps regular alerts at 4096 bytes, including our routing metadata.
        # Trim duplicated previews if Unicode or routing metadata reaches the cap.
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        while len(encoded) > 4096 and payload["notification"]["body"]:
            event["body"] = event["body"][:-40]
            payload["aps"]["alert"]["body"] = event["body"]
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > 4096:
            return 413, "PayloadTooLarge"
        host = ("api.sandbox.push.apple.com" if delivery["environment"] == "sandbox"
                else "api.push.apple.com")
        response = await self.client.post(
            f"https://{host}/3/device/{delivery['token']}", content=encoded,
            headers={"authorization": f"bearer {self.authorization()}",
                     "apns-topic": self.settings.apns_topic, "apns-push-type": "alert",
                     "apns-priority": "10", "apns-id": event["id"],
                     "apns-collapse-id": collapse_id,
                     "apns-expiration": str(int(delivery["expires_at"])),
                     "content-type": "application/json"},
        )
        try:
            reason = response.json().get("reason", "") if response.content else ""
        except (ValueError, AttributeError):
            reason = ""
        return response.status_code, reason

    async def close(self) -> None:
        await self.client.aclose()


class NotificationService:
    def __init__(self, store: Store, settings: Settings, profile_id: str = "default"):
        self.store = store
        self.settings = settings
        self.profile_id = profile_id
        self.sender = APNsSender(settings)

    def publish(
        self, kind: str, title: str, body: str, target: dict, event_key: str,
        *, guard: Callable[[sqlite3.Connection], None] | None = None,
    ) -> dict:
        return self.store.publish_notification(kind, title, body, target, event_key, guard=guard)

    async def deliver_pending(self) -> None:
        # Configuration edits become effective without restarting the archive worker.
        self.sender.settings = self.settings
        if not self.sender.configured:
            return
        for delivery in self.store.pending_notifications():
            if not self.store.notification_still_pending(delivery["id"], delivery["device_id"]):
                continue
            attempts = delivery["attempts"] + 1
            try:
                status, reason = await self.sender.send(delivery, self.profile_id)
            except (httpx.HTTPError, ValueError, jwt.PyJWTError):
                # Never log bearer credentials, device tokens, private keys, or message bodies.
                logger.warning("Push delivery failed; scheduled for retry")
                status, reason = 503, ""
            if status == 410 or reason in {"BadDeviceToken", "DeviceTokenNotForTopic"}:
                self.store.retire_notification_token(delivery["device_id"], delivery["token"])
                continue
            state = "sent" if status == 200 else "pending"
            if status == 413 or reason in {"BadPayload", "PayloadEmpty"}:
                state = "failed"
            self.store.finish_notification_delivery(
                delivery["id"], delivery["device_id"], state, attempts,
            )

    async def run(self) -> None:
        while True:
            try:
                await self.deliver_pending()
            except Exception:
                logger.warning("Push worker iteration failed; will retry")
            await asyncio.sleep(2)

    async def close(self) -> None:
        await self.sender.close()

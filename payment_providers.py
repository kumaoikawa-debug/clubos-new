from __future__ import annotations

import base64
import json
import os
import re
import secrets
import time
import urllib.parse
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass
class PaymentCreateResult:
    status: str
    provider_payment_id: str | None = None
    action: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None

    def as_dict(self):
        return asdict(self)


@dataclass
class RefundCreateResult:
    status: str
    provider_refund_id: str | None = None
    raw: dict[str, Any] | None = None

    def as_dict(self):
        return asdict(self)


def _safe_ref(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", ref or "").upper()


def _read_env_secret(ref: str, name: str, default: str = "") -> str:
    """Load a secret from env or an env-referenced file.

    Example credential ref `club_1_wx`:
      PAYMENT_CRED_CLUB_1_WX_PRIVATE_KEY=-----BEGIN...\n...
      PAYMENT_CRED_CLUB_1_WX_PRIVATE_KEY_FILE=/run/secrets/wx.pem
    """
    prefix = f"PAYMENT_CRED_{_safe_ref(ref)}_{name.upper()}"
    file_path = os.getenv(prefix + "_FILE", "").strip()
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    value = os.getenv(prefix, default)
    return value.replace("\\n", "\n").strip()


def _load_private_key(pem: str):
    if not pem:
        raise ValueError("payment private key is not configured")
    return serialization.load_pem_private_key(pem.encode(), password=None)


def _load_public_key(pem: str):
    if not pem:
        raise ValueError("payment public key is not configured")
    return serialization.load_pem_public_key(pem.encode())


def _rsa_sign(private_key_pem: str, message: str) -> str:
    key = _load_private_key(private_key_pem)
    sig = key.sign(message.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode("ascii")


def _rsa_verify(public_key_pem: str, message: str, signature_b64: str) -> bool:
    try:
        key = _load_public_key(public_key_pem)
        key.verify(base64.b64decode(signature_b64), message.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


def _cents(amount_yuan: float) -> int:
    return int(round(float(amount_yuan) * 100))


def _money(amount: float) -> str:
    return f"{float(amount):.2f}"


class LocalPaymentProvider:
    name = "local"

    def create_payment(self, *, account: dict[str, Any], merchant_order_no: str, amount: float,
                       subject: str, notify_url: str, return_url: str | None = None,
                       metadata: dict[str, Any] | None = None) -> PaymentCreateResult:
        return PaymentCreateResult(
            status="processing",
            provider_payment_id="local_" + uuid.uuid4().hex[:18],
            action={"type": "mock", "merchantOrderNo": merchant_order_no, "amount": amount},
            raw={"mock": True},
        )

    def create_refund(self, *, account: dict[str, Any], merchant_order_no: str, provider_transaction_id: str | None,
                      provider_refund_no: str, refund_amount: float, original_amount: float,
                      reason: str, notify_url: str) -> RefundCreateResult:
        return RefundCreateResult(status="succeeded", provider_refund_id="local_r_" + uuid.uuid4().hex[:16], raw={"mock": True})


class WeChatPayV3Provider:
    name = "wechatpay_v3"
    api_base = "https://api.mch.weixin.qq.com"

    def __init__(self, account: dict[str, Any]):
        self.account = account
        ref = str(account.get("credential_ref") or "")
        self.mchid = str(account.get("merchant_id") or "")
        self.appid = str(account.get("app_id") or "")
        self.serial_no = _read_env_secret(ref, "SERIAL_NO")
        self.private_key = _read_env_secret(ref, "PRIVATE_KEY")
        self.api_v3_key = _read_env_secret(ref, "API_V3_KEY")
        self.platform_public_key = _read_env_secret(ref, "PLATFORM_PUBLIC_KEY")
        self.timeout = float(os.getenv("PAYMENT_HTTP_TIMEOUT_SECONDS", "20"))
        if not self.mchid or not self.appid:
            raise ValueError("WeChat Pay account requires merchant_id and app_id")

    def _authorization(self, method: str, path_with_query: str, body: str) -> str:
        ts = str(int(time.time()))
        nonce = secrets.token_hex(16)
        message = f"{method}\n{path_with_query}\n{ts}\n{nonce}\n{body}\n"
        signature = _rsa_sign(self.private_key, message)
        return (
            'WECHATPAY2-SHA256-RSA2048 '
            f'mchid="{self.mchid}",nonce_str="{nonce}",signature="{signature}",'
            f'timestamp="{ts}",serial_no="{self.serial_no}"'
        )

    def _request(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        headers = {
            "Authorization": self._authorization(method, path, body),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "ClubOS-NEW/0.15",
        }
        with httpx.Client(timeout=self.timeout) as client:
            r = client.request(method, self.api_base + path, content=body.encode("utf-8"), headers=headers)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        if r.status_code >= 400:
            raise RuntimeError(f"WeChat Pay HTTP {r.status_code}: {data}")
        return data

    def create_payment(self, *, account: dict[str, Any], merchant_order_no: str, amount: float,
                       subject: str, notify_url: str, return_url: str | None = None,
                       metadata: dict[str, Any] | None = None) -> PaymentCreateResult:
        metadata = metadata or {}
        channel = str(account.get("channel") or "native").lower()
        path = {
            "native": "/v3/pay/transactions/native",
            "jsapi": "/v3/pay/transactions/jsapi",
            "h5": "/v3/pay/transactions/h5",
        }.get(channel)
        if not path:
            raise ValueError(f"Unsupported WeChat Pay channel: {channel}")
        body: dict[str, Any] = {
            "appid": self.appid,
            "mchid": self.mchid,
            "description": subject[:127],
            "out_trade_no": merchant_order_no,
            "notify_url": notify_url,
            "amount": {"total": _cents(amount), "currency": "CNY"},
        }
        if channel == "jsapi":
            openid = str(metadata.get("openid") or "")
            if not openid:
                raise ValueError("WeChat JSAPI payment requires payer openid")
            body["payer"] = {"openid": openid}
        elif channel == "h5":
            client_ip = str(metadata.get("clientIp") or metadata.get("client_ip") or "127.0.0.1")
            body["scene_info"] = {"payer_client_ip": client_ip, "h5_info": {"type": "Wap"}}
        data = self._request("POST", path, body)
        if channel == "native":
            action = {"type": "qrcode", "url": data.get("code_url"), "channel": "wechat_native"}
            provider_id = data.get("code_url")
        elif channel == "h5":
            h5_url = data.get("h5_url")
            if return_url and h5_url:
                sep = "&" if "?" in h5_url else "?"
                h5_url += sep + "redirect_url=" + urllib.parse.quote(return_url, safe="")
            action = {"type": "redirect", "url": h5_url, "channel": "wechat_h5"}
            provider_id = h5_url
        else:
            prepay_id = str(data.get("prepay_id") or "")
            ts = str(int(time.time()))
            nonce = secrets.token_hex(16)
            package = "prepay_id=" + prepay_id
            pay_sign = _rsa_sign(self.private_key, f"{self.appid}\n{ts}\n{nonce}\n{package}\n")
            action = {
                "type": "jsapi",
                "channel": "wechat_jsapi",
                "params": {"appId": self.appid, "timeStamp": ts, "nonceStr": nonce, "package": package,
                           "signType": "RSA", "paySign": pay_sign},
            }
            provider_id = prepay_id
        return PaymentCreateResult(status="processing", provider_payment_id=provider_id, action=action, raw=data)

    def create_refund(self, *, account: dict[str, Any], merchant_order_no: str, provider_transaction_id: str | None,
                      provider_refund_no: str, refund_amount: float, original_amount: float,
                      reason: str, notify_url: str) -> RefundCreateResult:
        body: dict[str, Any] = {
            "out_refund_no": provider_refund_no,
            "reason": reason[:80] if reason else "用户退款",
            "notify_url": notify_url,
            "amount": {"refund": _cents(refund_amount), "total": _cents(original_amount), "currency": "CNY"},
        }
        if provider_transaction_id:
            body["transaction_id"] = provider_transaction_id
        else:
            body["out_trade_no"] = merchant_order_no
        data = self._request("POST", "/v3/refund/domestic/refunds", body)
        status = str(data.get("status") or "PROCESSING").upper()
        mapped = "succeeded" if status == "SUCCESS" else ("failed" if status in ("CLOSED", "ABNORMAL") else "processing")
        return RefundCreateResult(status=mapped, provider_refund_id=str(data.get("refund_id") or provider_refund_no), raw=data)

    def verify_callback(self, headers: dict[str, str], raw_body: bytes) -> dict[str, Any]:
        h = {k.lower(): v for k, v in headers.items()}
        ts = h.get("wechatpay-timestamp", "")
        nonce = h.get("wechatpay-nonce", "")
        sig = h.get("wechatpay-signature", "")
        if not ts or not nonce or not sig:
            raise ValueError("missing WeChat callback signature headers")
        message = f"{ts}\n{nonce}\n{raw_body.decode('utf-8')}\n"
        if not _rsa_verify(self.platform_public_key, message, sig):
            raise ValueError("invalid WeChat callback signature")
        envelope = json.loads(raw_body.decode("utf-8"))
        resource = envelope.get("resource") or {}
        if not self.api_v3_key or len(self.api_v3_key.encode("utf-8")) != 32:
            raise ValueError("WeChat APIv3 key must be 32 bytes")
        aes = AESGCM(self.api_v3_key.encode("utf-8"))
        plain = aes.decrypt(
            str(resource.get("nonce") or "").encode("utf-8"),
            base64.b64decode(str(resource.get("ciphertext") or "")),
            str(resource.get("associated_data") or "").encode("utf-8") or None,
        )
        return {"envelope": envelope, "resource": json.loads(plain.decode("utf-8"))}


class AlipayProvider:
    name = "alipay"

    def __init__(self, account: dict[str, Any]):
        self.account = account
        ref = str(account.get("credential_ref") or "")
        self.app_id = str(account.get("app_id") or "")
        self.private_key = _read_env_secret(ref, "PRIVATE_KEY")
        self.alipay_public_key = _read_env_secret(ref, "ALIPAY_PUBLIC_KEY")
        self.gateway = _read_env_secret(ref, "GATEWAY", "https://openapi.alipay.com/gateway.do")
        if not self.app_id:
            raise ValueError("Alipay account requires app_id")

    def _sign_params(self, params: dict[str, Any]) -> tuple[str, dict[str, str]]:
        string_params = {k: str(v) for k, v in params.items() if v is not None and str(v) != "" and k not in ("sign", "sign_type")}
        content = "&".join(f"{k}={string_params[k]}" for k in sorted(string_params))
        signature = _rsa_sign(self.private_key, content)
        return signature, string_params

    def _gateway_url(self, params: dict[str, Any]) -> str:
        signature, normalized = self._sign_params(params)
        normalized["sign"] = signature
        normalized["sign_type"] = "RSA2"
        return self.gateway + "?" + urllib.parse.urlencode(normalized)

    def create_payment(self, *, account: dict[str, Any], merchant_order_no: str, amount: float,
                       subject: str, notify_url: str, return_url: str | None = None,
                       metadata: dict[str, Any] | None = None) -> PaymentCreateResult:
        channel = str(account.get("channel") or "wap").lower()
        method = "alipay.trade.page.pay" if channel == "page" else "alipay.trade.wap.pay"
        product_code = "FAST_INSTANT_TRADE_PAY" if channel == "page" else "QUICK_WAP_WAY"
        biz = {"out_trade_no": merchant_order_no, "total_amount": _money(amount), "subject": subject[:256], "product_code": product_code}
        params = {
            "app_id": self.app_id,
            "method": method,
            "format": "JSON",
            "charset": "utf-8",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "version": "1.0",
            "notify_url": notify_url,
            "return_url": return_url,
            "biz_content": json.dumps(biz, ensure_ascii=False, separators=(",", ":")),
        }
        url = self._gateway_url(params)
        return PaymentCreateResult(status="processing", provider_payment_id=merchant_order_no,
                                   action={"type": "redirect", "url": url, "channel": "alipay_" + channel}, raw={"method": method})

    def create_refund(self, *, account: dict[str, Any], merchant_order_no: str, provider_transaction_id: str | None,
                      provider_refund_no: str, refund_amount: float, original_amount: float,
                      reason: str, notify_url: str) -> RefundCreateResult:
        biz = {"out_trade_no": merchant_order_no, "refund_amount": _money(refund_amount), "out_request_no": provider_refund_no,
               "refund_reason": reason[:256] if reason else "用户退款"}
        params = {
            "app_id": self.app_id,
            "method": "alipay.trade.refund",
            "format": "JSON",
            "charset": "utf-8",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "version": "1.0",
            "biz_content": json.dumps(biz, ensure_ascii=False, separators=(",", ":")),
        }
        signature, normalized = self._sign_params(params)
        normalized["sign"] = signature
        normalized["sign_type"] = "RSA2"
        with httpx.Client(timeout=float(os.getenv("PAYMENT_HTTP_TIMEOUT_SECONDS", "20"))) as client:
            r = client.post(self.gateway, data=normalized)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        response = data.get("alipay_trade_refund_response") or data
        ok = str(response.get("code") or "") == "10000"
        status = "succeeded" if ok else "failed"
        return RefundCreateResult(status=status, provider_refund_id=str(response.get("trade_no") or provider_refund_no), raw=data)

    def verify_callback(self, form: dict[str, Any]) -> dict[str, Any]:
        signature = str(form.get("sign") or "")
        normalized = {k: str(v) for k, v in form.items() if k not in ("sign", "sign_type") and v is not None}
        content = "&".join(f"{k}={normalized[k]}" for k in sorted(normalized))
        if not signature or not _rsa_verify(self.alipay_public_key, content, signature):
            raise ValueError("invalid Alipay callback signature")
        return normalized


class PaymentAccountService:
    """Routes activity money to the club account and Gear money to the platform account.

    Secrets are deliberately not stored in SQLite. `credential_ref` only points to env/vault material.
    """

    def get_scope_account(self, c, *, scope_type: str, scope_id: int | None = None) -> dict[str, Any] | None:
        if scope_type == "platform":
            r = c.execute("SELECT * FROM payment_accounts WHERE scope_type='platform' AND enabled=1 ORDER BY id DESC LIMIT 1").fetchone()
        else:
            r = c.execute("SELECT * FROM payment_accounts WHERE scope_type='club' AND scope_id=? AND enabled=1 ORDER BY id DESC LIMIT 1", (scope_id,)).fetchone()
        return dict(r) if r else None

    def resolve_for_checkout(self, c, checkout: dict[str, Any]) -> dict[str, Any]:
        if checkout.get("kind") == "activity":
            account = self.get_scope_account(c, scope_type="club", scope_id=int(checkout.get("club_id") or 0))
            if not account:
                raise ValueError("该俱乐部尚未配置活动收款账户")
            return account
        account = self.get_scope_account(c, scope_type="platform")
        if not account:
            raise ValueError("总平台尚未配置装备商城收款账户")
        return account

    def upsert(self, c, *, scope_type: str, scope_id: int | None, provider: str, channel: str,
               merchant_id: str | None = None, app_id: str | None = None, credential_ref: str = "",
               enabled: bool = True, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if scope_type not in ("club", "platform"):
            raise ValueError("invalid payment account scope")
        if provider not in ("local", "wechatpay_v3", "alipay"):
            raise ValueError("unsupported payment provider")
        if scope_type == "club" and not scope_id:
            raise ValueError("club payment account requires scope_id")
        r = c.execute("SELECT id FROM payment_accounts WHERE scope_type=? AND COALESCE(scope_id,0)=COALESCE(?,0) ORDER BY id DESC LIMIT 1", (scope_type, scope_id)).fetchone()
        if r:
            c.execute("""UPDATE payment_accounts SET provider=?,channel=?,merchant_id=?,app_id=?,credential_ref=?,enabled=?,metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                      (provider, channel, merchant_id, app_id, credential_ref, 1 if enabled else 0,
                       json.dumps(metadata or {}, ensure_ascii=False), r[0]))
            account_id = int(r[0])
        else:
            c.execute("""INSERT INTO payment_accounts(scope_type,scope_id,provider,channel,merchant_id,app_id,credential_ref,enabled,metadata_json)
                         VALUES(?,?,?,?,?,?,?,?,?)""", (scope_type, scope_id, provider, channel, merchant_id, app_id,
                                                        credential_ref, 1 if enabled else 0, json.dumps(metadata or {}, ensure_ascii=False)))
            account_id = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])
        return dict(c.execute("SELECT * FROM payment_accounts WHERE id=?", (account_id,)).fetchone())


def provider_for_account(account: dict[str, Any]):
    provider = str(account.get("provider") or "local")
    if provider == "local":
        return LocalPaymentProvider()
    if provider == "wechatpay_v3":
        return WeChatPayV3Provider(account)
    if provider == "alipay":
        return AlipayProvider(account)
    raise ValueError(f"Unsupported payment provider: {provider}")


def merchant_order_no() -> str:
    # WeChat Pay out_trade_no max length is 32; keep ours at 30 chars.
    return "C" + uuid.uuid4().hex[:29]


def provider_refund_no() -> str:
    return "R" + uuid.uuid4().hex[:29]

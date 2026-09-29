from __future__ import annotations
import os
import uuid
from dataclasses import dataclass
from typing import Any

MOCK_PREMIUM_DEFAULT = 10.0  # 演示用：每人保费（人民币），真实保费由保险公司返回


@dataclass
class EnrollRequest:
    participant_id: int
    club_id: int
    occurrence_id: int
    name: str
    id_number: str | None
    phone: str | None
    activity_title: str
    effective_at: str
    expire_at: str
    premium_amount: float


@dataclass
class EnrollResult:
    ok: bool
    policy_no: str | None = None
    effective_at: str | None = None
    expire_at: str | None = None
    premium_amount: float = 0.0
    raw: dict = None  # type: ignore
    error: str | None = None


@dataclass
class CancelRequest:
    participant_id: int
    club_id: int
    occurrence_id: int
    policy_no: str
    effective_at: str | None


@dataclass
class CancelResult:
    ok: bool
    status: str = "cancelled"  # cancelled | failed
    premium_refunded: float = 0.0
    raw: dict = None  # type: ignore
    error: str | None = None


class InsuranceProvider:
    """保险公司适配器基类。真实提供方继承并实现 _enroll / _cancel。"""

    name = "base"

    def enroll(self, req: EnrollRequest) -> EnrollResult:  # pragma: no cover
        raise NotImplementedError

    def cancel(self, req: CancelRequest) -> CancelResult:  # pragma: no cover
        raise NotImplementedError

    def get_status(self, policy_no: str) -> dict:  # pragma: no cover
        raise NotImplementedError


class MockInsuranceProvider(InsuranceProvider):
    """演示用保险方：立即承保、合成保单号、生效窗口按团期。无需网络。"""

    name = "mock"

    def enroll(self, req: EnrollRequest) -> EnrollResult:
        policy_no = "MOCK-POL-" + uuid.uuid4().hex[:12].upper()
        return EnrollResult(
            ok=True,
            policy_no=policy_no,
            effective_at=req.effective_at,
            expire_at=req.expire_at,
            premium_amount=req.premium_amount,
            raw={"mock": True, "provider": "mock"},
        )

    def cancel(self, req: CancelRequest) -> CancelResult:
        return CancelResult(
            ok=True,
            status="cancelled",
            premium_refunded=0.0,
            raw={"mock": True, "provider": "mock", "policyNo": req.policy_no},
        )

    def get_status(self, policy_no: str) -> dict:
        return {"policyNo": policy_no, "status": "insured", "provider": "mock"}


class HttpInsuranceProvider(InsuranceProvider):
    """真实保险方模板。配好 base_url / api_key 后实现两个调用即可，编排器一行不用动。"""

    name = "http"

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    def enroll(self, req: EnrollRequest) -> EnrollResult:  # pragma: no cover
        # TODO: POST {base_url}/policies，解析 policyNo / effective / expire / premium
        raise NotImplementedError("real insurance provider not configured")

    def cancel(self, req: CancelRequest) -> CancelResult:  # pragma: no cover
        raise NotImplementedError("real insurance provider not configured")

    def get_status(self, policy_no: str) -> dict:  # pragma: no cover
        raise NotImplementedError("real insurance provider not configured")


def insurance_provider() -> InsuranceProvider:
    """配置驱动工厂，复用 ai_gateway / payment_providers 的模式。

    INSURANCE_PROVIDER=mock（默认）  → MockInsuranceProvider
    INSURANCE_PROVIDER=http          → HttpInsuranceProvider（需配 API_BASE / API_KEY）
    未知值回退 mock，保证自动化不中断。
    """
    provider = (os.getenv("INSURANCE_PROVIDER") or "mock").strip().lower()
    if provider in ("", "mock"):
        return MockInsuranceProvider()
    if provider == "http":
        base = os.getenv("INSURANCE_API_BASE") or ""
        key = os.getenv("INSURANCE_API_KEY") or ""
        return HttpInsuranceProvider(base_url=base, api_key=key)
    return MockInsuranceProvider()


def premium_for(policy: dict) -> float:
    """演示保费（每人）。真实提供方由投保接口返回保费。"""
    return float(policy.get("insurancePremiumPerPerson") or os.getenv("INSURANCE_MOCK_PREMIUM") or MOCK_PREMIUM_DEFAULT)

from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from clubos_domain.insurance_providers import (
    insurance_provider,
    premium_for,
    EnrollRequest,
    CancelRequest,
)

logger = logging.getLogger("insurance")

VALID_STATUSES = {
    "pending", "enrolling", "insured", "cancelling",
    "cancelled", "failed", "cancel_failed", "not_required",
}


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def effective_window(occurrence: dict) -> tuple[str | None, str | None]:
    """保险生效窗口对齐团期：生效=出发，失效=结束+1天宽限。"""
    start = _parse_dt(occurrence.get("start_at"))
    end = _parse_dt(occurrence.get("end_at"))
    if not start:
        return None, None
    expire = (end + timedelta(days=1)) if end else (start + timedelta(days=1))
    return start.isoformat(), expire.isoformat()


def insurance_bearer(activity: dict) -> str:
    """保费承担方：club（俱乐部承担）/ customer（顾客另付）。默认 club。"""
    try:
        from clubos_domain.participants import ParticipantService
        return ParticipantService().from_activity(activity).data.get("insuranceBearer") or "club"
    except Exception:
        return "club"


class InsuranceOrchestrator:
    """保险自动化编排器：在支付成功 / 退款 finalize 时触发，对接保险适配器。

    - 幂等：同一参加人+团期不会重复投保；同一保单不会重复退保。
    - 失败不阻断主流程：投保/退保失败只把状态置 failed / cancel_failed 并写日志，
      由运营在现有「批量/手动」入口兜底。
    """

    # ---- 投保 ----
    def enroll_pending_for_registration(self, c, *, registration_id: int, occurrence: dict, activity: dict) -> list[int]:
        try:
            from clubos_domain.participants import ParticipantService
            policy = ParticipantService().from_activity(activity).data
        except Exception:
            policy = {}
        if not policy.get("insuranceRequired", True):
            return []
        rows = c.execute(
            "SELECT * FROM registration_participants WHERE registration_id=? AND status='active' AND insurance_status='pending'",
            (registration_id,),
        ).fetchall()
        done = []
        for p in rows:
            self.enroll_participant(c, participant=dict(p), occurrence=occurrence, activity=activity, policy=policy)
            done.append(int(p["id"]))
        return done

    def enroll_participant(self, c, *, participant: dict, occurrence: dict, activity: dict, policy: dict | None = None) -> dict:
        if policy is None:
            from clubos_domain.participants import ParticipantService
            policy = ParticipantService().from_activity(activity).data
        if not policy.get("insuranceRequired", True):
            return {"ok": False, "skipped": "insurance not required"}
        effective_at, expire_at = effective_window(occurrence)
        premium = premium_for(policy)
        pid = int(participant["id"])
        job = self._new_job(
            c, participant_id=pid, club_id=int(participant["club_id"]),
            registration_id=int(participant["registration_id"]), occurrence_id=int(occurrence["id"]),
            action="enroll", effective_at=effective_at, expire_at=expire_at, premium_amount=premium,
        )
        c.execute("UPDATE registration_participants SET insurance_status='enrolling',updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
        try:
            provider = insurance_provider()
            req = EnrollRequest(
                participant_id=pid, club_id=int(participant["club_id"]), occurrence_id=int(occurrence["id"]),
                name=participant.get("name") or "", id_number=participant.get("id_number") or None,
                phone=participant.get("phone") or None, activity_title=activity.get("title") or "",
                effective_at=effective_at or "", expire_at=expire_at or "", premium_amount=premium,
            )
            res = provider.enroll(req)
            if res.ok:
                c.execute(
                    """UPDATE registration_participants SET insurance_status='insured',insurance_provider=?,
                        insurance_policy_no=?,insured_at=CURRENT_TIMESTAMP,effective_at=?,expire_at=?,
                        premium_amount=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (provider.name, res.policy_no, effective_at, expire_at, premium, pid),
                )
                self._job_done(c, job["id"], status="done", policy_no=res.policy_no,
                               payload={"policyNo": res.policy_no, "effective_at": effective_at, "expire_at": expire_at})
                return {"ok": True, "policyNo": res.policy_no, "participantId": pid}
            c.execute("UPDATE registration_participants SET insurance_status='failed',updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
            self._job_done(c, job["id"], status="failed", error=res.error)
            return {"ok": False, "error": res.error}
        except Exception as e:  # 网络/适配层异常
            c.execute("UPDATE registration_participants SET insurance_status='failed',updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
            self._job_done(c, job["id"], status="failed", error=str(e)[:300])
            logger.warning("auto-enroll failed pid=%s: %s", pid, e)
            return {"ok": False, "error": str(e)[:300]}

    # ---- 退保 ----
    def cancel_for_participant(self, c, *, participant: dict, occurrence: dict, bearer: str) -> dict:
        pid = int(participant["id"])
        policy_no = participant.get("insurance_policy_no")
        status_now = participant.get("insurance_status")
        if status_now in ("not_required", "cancelled", "cancel_failed"):
            return {"ok": True, "skipped": True, "reason": "nothing to cancel"}
        # 从未成功投保（只有 pending/enrolling 且无保单号）：直接标记已退保，不调提供方
        if not policy_no and status_now != "enrolling":
            c.execute("UPDATE registration_participants SET insurance_status='cancelled',premium_refunded=0,updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
            return {"ok": True, "skipped": True, "reason": "no policy"}
        start = _parse_dt(occurrence.get("start_at"))
        departed = bool(start and start <= datetime.now())
        job = self._new_job(
            c, participant_id=pid, club_id=int(participant["club_id"]),
            registration_id=int(participant["registration_id"]), occurrence_id=int(occurrence["id"]),
            action="cancel", policy_no=policy_no, effective_at=participant.get("effective_at"),
            expire_at=participant.get("expire_at"), provider=participant.get("insurance_provider"),
        )
        if departed:
            # 规则：出发后不退保（避免对已生效保单做无效退保）
            self._job_done(c, job["id"], status="skipped", payload={"reason": "departed"})
            return {"ok": True, "skipped": True, "reason": "departed"}
        c.execute("UPDATE registration_participants SET insurance_status='cancelling',updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
        try:
            provider = insurance_provider()
            res = provider.cancel(CancelRequest(
                participant_id=pid, club_id=int(participant["club_id"]), occurrence_id=int(occurrence["id"]),
                policy_no=policy_no or "", effective_at=participant.get("effective_at"),
            ))
            if res.ok:
                premium_refunded = float(participant.get("premium_amount") or 0) if bearer == "customer" else 0.0
                c.execute(
                    "UPDATE registration_participants SET insurance_status='cancelled',premium_refunded=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (premium_refunded, pid),
                )
                self._job_done(c, job["id"], status="done", policy_no=policy_no, payload={"premiumRefunded": premium_refunded})
                return {"ok": True, "cancelled": True, "premiumRefunded": premium_refunded, "participantId": pid}
            c.execute("UPDATE registration_participants SET insurance_status='cancel_failed',updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
            self._job_done(c, job["id"], status="failed", error=res.error)
            return {"ok": False, "error": res.error}
        except Exception as e:
            c.execute("UPDATE registration_participants SET insurance_status='cancel_failed',updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
            self._job_done(c, job["id"], status="failed", error=str(e)[:300])
            logger.warning("auto-cancel failed pid=%s: %s", pid, e)
            return {"ok": False, "error": str(e)[:300]}

    def cancel_for_registration_refund(self, c, *, registration_id: int, occurrence: dict, bearer: str) -> list[dict]:
        rows = c.execute(
            "SELECT * FROM registration_participants WHERE registration_id=? AND status='active' AND insurance_status IN ('insured','enrolling')",
            (registration_id,),
        ).fetchall()
        return [self.cancel_for_participant(c, participant=dict(p), occurrence=occurrence, bearer=bearer) for p in rows]

    def cancel_participants(self, c, *, participants: list[dict], occurrence: dict, bearer: str) -> list[dict]:
        """对一组已捕获（尚未改 status 前）的参加人批量退保，供退款 finalize 调用。"""
        return [self.cancel_for_participant(c, participant=p, occurrence=occurrence, bearer=bearer) for p in participants]

    # ---- job 审计 ----
    def _new_job(self, c, *, participant_id, club_id, registration_id, occurrence_id, action,
                 policy_no=None, effective_at=None, expire_at=None, premium_amount=0.0, provider=None) -> dict:
        c.execute(
            """INSERT INTO insurance_jobs(club_id,participant_id,registration_id,occurrence_id,action,
                status,effective_at,expire_at,premium_amount,provider,policy_no)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (club_id, participant_id, registration_id, occurrence_id, action, "processing",
             effective_at, expire_at, premium_amount, provider, policy_no),
        )
        return {"id": int(c.execute("SELECT last_insert_rowid()").fetchone()[0])}

    def _job_done(self, c, job_id: int, *, status: str, policy_no=None, error=None, payload=None):
        c.execute(
            """UPDATE insurance_jobs SET status=?,policy_no=COALESCE(?,policy_no),error=?,provider_payload_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (status, policy_no, error, json.dumps(payload or {}, ensure_ascii=False), job_id),
        )

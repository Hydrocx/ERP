"""Nhận xét bằng ngôn ngữ tự nhiên từ kết quả các mô hình ML.

Mô hình ML tính toàn bộ số liệu; LLM chỉ diễn giải. Mọi con số trong văn bản AI được đối chiếu lại với `facts`
(dùng lại `reports.services.check_numbers`); nếu không gọi được AI thì dùng mẫu câu cố định.
"""

import logging
from datetime import timedelta

from ai.client import AIError, AIUnavailable, chat_json
from ai.models import AICallLog
from ai.prompts import INSIGHT_SCHEMA, INSIGHT_SYSTEM
from reports.services import check_numbers

logger = logging.getLogger(__name__)


def vnd_text(value):
    value = float(value)
    if abs(value) >= 1e9:
        return f"{value / 1e9:.2f} tỷ".replace(".", ",")
    return f"{value / 1e6:.0f} triệu"


def build_facts(summaries, as_of):
    """Gom tóm tắt của từng bài toán thành một dict số liệu (tiền ở dạng VNĐ nguyên để kiểm tra số)."""
    start = as_of - timedelta(days=27)
    prev_end = start - timedelta(days=1)
    return {
        "period": {"start": start.isoformat(), "end": as_of.isoformat()},
        "previous_period": {"start": (prev_end - timedelta(days=27)).isoformat(), "end": prev_end.isoformat()},
        **{task: summary for task, summary in summaries.items() if summary},
    }


def fallback_insights(facts):
    """Nhận xét theo mẫu, dùng khi không có AI."""
    out = []
    fc = facts.get("forecast", {}).get("total")
    if fc:
        direction = "tăng" if fc["change_pct"] >= 0 else "giảm"
        out.append(("forecast", "Dự báo doanh thu 4 tuần tới",
                    f"Mô hình {fc['champion']} dự báo doanh thu 4 tuần tới đạt {vnd_text(fc['forecast_next_28d_revenue'])}, "
                    f"{direction} {abs(fc['change_pct']):.1f}% so với 4 tuần gần nhất "
                    f"({vnd_text(fc['actual_last_28d_revenue'])})."))
        gain = fc["skill_vs_naive_pct"]
        verdict = "tốt hơn" if gain > 0 else "không tốt hơn"
        out.append(("model_quality", "Chất lượng mô hình dự báo",
                    f"Sai số WAPE theo tuần của {fc['champion']} là {fc['wape_weekly_pct']:.1f}%, "
                    f"{verdict} mốc so sánh seasonal naive ({fc['baseline_wape_weekly_pct']:.1f}%)."))
    ch = facts.get("churn")
    if ch:
        out.append(("customer", "Khách hàng có nguy cơ rời bỏ",
                    f"{ch['high_risk']} khách hàng có nguy cơ cao, chiếm {vnd_text(ch['revenue_at_risk_365'])} doanh số 365 ngày; "
                    f"mô hình {ch['champion']} đạt AUC {ch['auc']:.2f} trên tập kiểm tra theo thời gian."))
    an = facts.get("anomaly")
    if an:
        out.append(("anomaly", "Ngày doanh thu bất thường",
                    f"Phát hiện {an['events']} ngày bất thường trong 180 ngày gần nhất, "
                    f"trong đó {an['high_severity']} ngày mức cao."))
    bk = facts.get("basket", {}).get("top_rule")
    if bk:
        out.append(("basket", "Cặp sản phẩm hay mua kèm",
                    f"Khách mua {bk['antecedent_name']} thường mua kèm {bk['consequent_name']} "
                    f"(độ tin cậy {bk['confidence_pct']:.1f}%, lift {bk['lift']:.2f})."))
    return out


def generate_insights(facts, use_ai=True, user=None):
    """Trả về (danh sách {kind,title,body,warnings}, đã_dùng_AI, tên_model_hoặc_'template')."""
    items, used_ai, label = None, False, "template"
    if use_ai:
        try:
            result = chat_json(AICallLog.Feature.INSIGHT, INSIGHT_SYSTEM, facts, INSIGHT_SCHEMA,
                               schema_name="ml_insights", user=user, feature_flag="enable_report")
            items = [{"kind": i["kind"], "title": i["title"].strip(), "body": i["body"].strip()}
                     for i in result.get("insights", []) if i.get("title") and i.get("body")]
            if items:
                used_ai = True
                from ai.client import get_settings
                label = get_settings().model_name
        except (AIUnavailable, AIError) as exc:
            logger.info("Insight AI skipped: %s", exc)
    if not items:
        items = [{"kind": k, "title": t, "body": b} for k, t, b in fallback_insights(facts)]
    for item in items:
        item["warnings"] = check_numbers([item["body"]], facts) if used_ai else []
    return items, used_ai, label

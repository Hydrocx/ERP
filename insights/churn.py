"""Dự báo khách hàng rời bỏ: "sẽ không mua gì trong 60 ngày tới".

Dựng dữ liệu theo mốc thời gian (snapshot):
  * Tại mỗi mốc `c`, chỉ dùng các đơn có ngày <= c để tính đặc trưng, nhãn = không có đơn nào trong (c, c+60].
  * Chỉ xét khách "đang hoạt động": có ít nhất một đơn trong 180 ngày trước mốc.
Chia tập theo thời gian (không xáo trộn ngẫu nhiên) để tránh rò rỉ dữ liệu:
  * Tập kiểm tra: mốc T = as_of - 60 (nhãn nằm trọn trong (T, as_of]).
  * Tập huấn luyện: các mốc c <= as_of - 120, cách nhau 30 ngày, nên nhãn của chúng kết thúc trước T.
Mô hình: luật `recency` (mốc so sánh, không huấn luyện), Logistic Regression, Gradient Boosting.
"""

import warnings

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HORIZON_DAYS = 60
ACTIVE_WINDOW = 180
CUTOFF_STEP = 30
# Mức rủi ro theo thứ hạng trong nhóm khách đang hoạt động: 20% điểm cao nhất là "Cao" (đúng nhóm mà
# precision_top20 đo trên tập kiểm tra), 30% kế tiếp là "Trung bình".
RISK_HIGH_SHARE, RISK_MEDIUM_SHARE = 0.2, 0.3
CHURNED_BAND = "Đã rời bỏ (>180 ngày không mua)"

FEATURES = ["recency_days", "freq_30", "freq_60", "freq_90", "freq_180", "monetary_90", "monetary_180",
            "avg_gap_days", "freq_trend", "tenure_days"]
FEATURE_LABELS = {
    "recency_days": "lâu chưa mua", "freq_30": "ít đơn 30 ngày qua", "freq_60": "ít đơn 60 ngày qua",
    "freq_90": "ít đơn 90 ngày qua", "freq_180": "ít đơn 180 ngày qua",
    "monetary_90": "doanh số 90 ngày thấp", "monetary_180": "doanh số 180 ngày thấp",
    "avg_gap_days": "khoảng cách giữa các đơn dài", "freq_trend": "tần suất mua đang giảm",
    "tenure_days": "khách mới / ít lịch sử",
}


def _count_after(days, threshold):
    return int(len(days) - np.searchsorted(days, threshold, side="right"))


def snapshot(customers, cutoff, with_label=True, horizon=HORIZON_DAYS):
    """Đặc trưng (và nhãn) của khách đang hoạt động tại mốc `cutoff` (ordinal)."""
    ids, rows, labels = [], [], []
    for customer_id, (days, revenue) in customers.items():
        upto = int(np.searchsorted(days, cutoff, side="right"))
        if upto == 0 or cutoff - days[upto - 1] > ACTIVE_WINDOW:
            continue
        d, r = days[:upto], revenue[:upto]
        cum = np.concatenate([[0.0], np.cumsum(r)])

        def money_since(window):
            return float(cum[-1] - cum[np.searchsorted(d, cutoff - window, side="right")])

        recent = d[d > cutoff - ACTIVE_WINDOW]
        avg_gap = float(np.diff(recent).mean()) if len(recent) > 1 else float(ACTIVE_WINDOW)
        f60, f120 = _count_after(d, cutoff - 60), _count_after(d, cutoff - 120)
        rows.append([
            cutoff - d[-1], _count_after(d, cutoff - 30), f60, _count_after(d, cutoff - 90),
            _count_after(d, cutoff - 180), money_since(90), money_since(180), avg_gap,
            (f60 - (f120 - f60)) / ((f120 - f60) + 1), cutoff - d[0],
        ])
        ids.append(customer_id)
        if with_label:
            labels.append(0 if np.searchsorted(days, cutoff + horizon, side="right") > upto else 1)
    x = np.array(rows, dtype=float).reshape(-1, len(FEATURES))
    return ids, x, (np.array(labels) if with_label else None)


def build_datasets(customers, as_of_ordinal, first_ordinal):
    """(train_x, train_y, test_x, test_y, test_ids) theo quy tắc chia tập theo thời gian."""
    test_cutoff = as_of_ordinal - HORIZON_DAYS
    xs, ys = [], []
    cutoff = as_of_ordinal - 2 * HORIZON_DAYS
    while cutoff - first_ordinal >= ACTIVE_WINDOW:
        _, x, y = snapshot(customers, cutoff)
        if len(y):
            xs.append(x)
            ys.append(y)
        cutoff -= CUTOFF_STEP
    if not xs:
        raise ValueError("Không đủ lịch sử để dựng tập huấn luyện rời bỏ")
    test_ids, test_x, test_y = snapshot(customers, test_cutoff)
    return np.vstack(xs), np.concatenate(ys), test_x, test_y, test_ids


def make_models():
    return {
        "logistic": make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000)),
        "gbm": HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                              min_samples_leaf=20, random_state=0),
    }


def precision_at_top(y_true, score, share=0.2):
    k = max(1, int(round(len(y_true) * share)))
    top = np.argsort(-score, kind="stable")[:k]
    return float(np.mean(y_true[top]))


def evaluate(train_x, train_y, test_x, test_y):
    """{algorithm: {auc, pr_auc, precision_top20, brier?}} cùng mốc so sánh `recency_rule`."""
    results = {}
    recency = test_x[:, FEATURES.index("recency_days")]
    results["recency_rule"] = _scores(test_y, recency)
    fitted = {}
    for name, model in make_models().items():
        model.fit(train_x, train_y)
        prob = model.predict_proba(test_x)[:, 1]
        results[name] = {**_scores(test_y, prob), "brier": float(brier_score_loss(test_y, prob))}
        fitted[name] = model
    champion = max((n for n in results), key=lambda n: results[n]["auc"])
    return results, champion, fitted


def _scores(y_true, score):
    if len(set(y_true.tolist())) < 2:
        return {"auc": float("nan"), "pr_auc": float("nan"), "precision_top20": float("nan")}
    return {"auc": float(roc_auc_score(y_true, score)), "pr_auc": float(average_precision_score(y_true, score)),
            "precision_top20": precision_at_top(y_true, score)}


def feature_importance(model, test_x, test_y, seed=0):
    """Mức quan trọng theo hoán vị (giảm AUC khi xáo trộn một đặc trưng) trên tập kiểm tra."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = permutation_importance(model, test_x, test_y, scoring="roc_auc", n_repeats=10,
                                        random_state=seed)
    return {f: float(v) for f, v in zip(FEATURES, result.importances_mean)}


def final_scores(customers, as_of_ordinal, first_ordinal, champion):
    """Huấn luyện lại champion trên mọi nhãn đã biết và chấm điểm khách đang hoạt động tại `as_of`."""
    train_x, train_y, test_x, test_y, _ = build_datasets(customers, as_of_ordinal, first_ordinal)
    x_all, y_all = np.vstack([train_x, test_x]), np.concatenate([train_y, test_y])
    models = make_models()
    model = models.get(champion, models["gbm"])
    model.fit(x_all, y_all)
    ids, x, _ = snapshot(customers, as_of_ordinal, with_label=False)
    prob = model.predict_proba(x)[:, 1] if len(ids) else np.array([])

    linear = models["logistic"].fit(x_all, y_all)
    scaler, lr = linear.steps[0][1], linear.steps[1][1]
    contrib = scaler.transform(x) * lr.coef_[0] if len(ids) else np.zeros((0, len(FEATURES)))
    drivers = [FEATURE_LABELS[FEATURES[int(i)]] if contrib[row, int(i)] > 0 else "" for row, i in
               enumerate(np.argmax(contrib, axis=1))] if len(ids) else []
    return ids, x, prob, drivers


def risk_bands(probs):
    """Mức rủi ro cho từng xác suất (None = khách đã rời bỏ, không chấm điểm).

    Dùng thứ hạng thay vì ngưỡng xác suất cố định: khi mô hình chỉ phân biệt vừa phải, xác suất hiếm khi vượt
    ngưỡng như 0,6 nên danh sách "Cao" sẽ rỗng; còn danh sách theo thứ hạng luôn có cỡ vừa sức đội bán hàng
    và chất lượng của nó chính là precision_top20 đã đo.
    """
    scored = [i for i, p in enumerate(probs) if p is not None]
    order = sorted(scored, key=lambda i: -probs[i])                  # sort ổn định, giống precision_at_top
    n_high = int(round(len(order) * RISK_HIGH_SHARE))
    n_medium = int(round(len(order) * (RISK_HIGH_SHARE + RISK_MEDIUM_SHARE))) - n_high
    bands = [CHURNED_BAND] * len(probs)
    for rank, i in enumerate(order):
        bands[i] = "Cao" if rank < n_high else "Trung bình" if rank < n_high + n_medium else "Thấp"
    return bands

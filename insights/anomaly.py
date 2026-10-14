"""Phát hiện ngày doanh thu bất thường (theo từng kho) bằng hai phương pháp khác nhau.

  robust_z          z-score bền vững: kỳ vọng = trung vị cùng thứ trong 8 tuần trước, thang đo = MAD của
                    sai số 56 ngày trước; gắn cờ khi |z| >= 3.5.
  isolation_forest  Isolation Forest (scikit-learn) trên [log doanh thu, doanh thu/kỳ vọng, chênh so với 7 ngày
                    trước]; gắn cờ ~5% ngày bất thường nhất.

Dữ liệu thật không có nhãn bất thường nên đánh giá bằng cách CHÈN bất thường giả (tăng vọt x2-3.5 hoặc tụt còn
10-40%) vào 180 ngày cuối rồi đo precision/recall. Ngày bất thường tự nhiên của dữ liệu mô phỏng cũng bị tính là
dương tính giả, nên precision đo được là cận dưới thận trọng.
"""

import warnings

import numpy as np
from sklearn.ensemble import IsolationForest

WEEKS = 8
SCALE_WINDOW = 56
Z_THRESHOLD = 3.5
Z_HIGH = 5.0
IF_CONTAMINATION = 0.05
EVAL_DAYS = 180
INJECT_RATE = 0.06
START = WEEKS * 7 + 28     # cần đủ lịch sử trước khi chấm điểm


def expected_values(y, weeks=WEEKS):
    y = np.asarray(y, dtype=float)
    out = np.full(len(y), np.nan)
    offsets = 7 * np.arange(1, weeks + 1)
    for t in range(weeks * 7, len(y)):
        out[t] = np.median(y[t - offsets])
    return out


def robust_z(y, exp=None):
    y = np.asarray(y, dtype=float)
    exp = expected_values(y) if exp is None else exp
    resid = y - exp
    z = np.full(len(y), np.nan)
    for t in range(START, len(y)):
        window = resid[t - SCALE_WINDOW:t]
        window = window[~np.isnan(window)]
        if len(window) < 14:
            continue
        mad = 1.4826 * np.median(np.abs(window - np.median(window)))
        if mad > 0:
            z[t] = resid[t] / mad
    return z, exp


def isolation_scores(y, exp, contamination=IF_CONTAMINATION, seed=0):
    y = np.asarray(y, dtype=float)
    n = len(y)
    valid = np.arange(START, n)
    ratio = np.clip(y[valid] / np.maximum(exp[valid], 1.0), 0, 5)
    delta7 = (y[valid] - y[valid - 7]) / np.maximum(exp[valid], 1.0)
    x = np.column_stack([np.log1p(y[valid]), ratio, delta7])
    model = IsolationForest(n_estimators=200, contamination=contamination, random_state=seed).fit(x)
    score = np.full(n, np.nan)
    flag = np.zeros(n, dtype=bool)
    score[valid] = -model.score_samples(x)
    flag[valid] = model.predict(x) == -1
    return score, flag


def detect(y):
    """Trả về dict thuật toán -> (cờ, điểm) cùng `expected` và `z`."""
    z, exp = robust_z(y)
    with np.errstate(invalid="ignore"):
        flag_z = np.abs(z) >= Z_THRESHOLD
    score_if, flag_if = isolation_scores(y, exp)
    return {"robust_z": (np.nan_to_num(flag_z), np.abs(z)), "isolation_forest": (flag_if, score_if)}, exp, z


def inject(y, rng, rate=INJECT_RATE, days=EVAL_DAYS):
    y = np.array(y, dtype=float)
    n = len(y)
    candidates = np.arange(n - days, n)
    chosen = rng.choice(candidates, size=max(1, int(days * rate)), replace=False)
    truth = np.zeros(n, dtype=bool)
    for i, idx in enumerate(chosen):
        y[idx] *= rng.uniform(2.0, 3.5) if i % 2 == 0 else rng.uniform(0.1, 0.4)
        truth[idx] = True
    return y, truth


def evaluate(series_list, seeds=(0, 1, 2, 3, 4)):
    """Precision / recall / F1 trung bình của từng phương pháp trên các chuỗi và nhiều lần chèn."""
    tally = {}
    for y in series_list:
        for seed in seeds:
            rng = np.random.default_rng(seed)
            injected, truth = inject(y, rng)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                detectors, _, _ = detect(injected)
            window = np.zeros(len(y), dtype=bool)
            window[len(y) - EVAL_DAYS:] = True
            for name, (flag, _) in detectors.items():
                predicted = flag & window
                tp = int((predicted & truth).sum())
                precision = tp / predicted.sum() if predicted.sum() else 0.0
                recall = tp / truth.sum()
                f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
                tally.setdefault(name, []).append((precision, recall, f1))
    return {name: dict(zip(("precision", "recall", "f1"), np.mean(vals, axis=0).tolist()))
            for name, vals in tally.items()}

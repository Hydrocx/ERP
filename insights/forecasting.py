"""Dự báo doanh thu theo ngày: 3 thuật toán, backtest cuốn chiếu và chọn thuật toán tốt nhất.

Thuật toán (đều dự báo trực tiếp `horizon` ngày tiếp theo):
  seasonal_naive  mốc so sánh: lặp lại tuần cuối cùng.
  holt_winters    làm mượt mũ cộng tính, mùa vụ tuần, xu hướng tắt dần; tự viết bằng numpy,
                  tham số chọn bằng lưới tìm kiếm trên sai số một-bước của tập huấn luyện.
  gbm             Gradient Boosting (scikit-learn) trên đặc trưng độ trễ >= 28 ngày, lịch và
                  doanh thu cùng kỳ năm trước (lag 364), nên dự báo trực tiếp được 28 ngày.

Đánh giá: backtest cuốn chiếu (rolling-origin) `folds` lần, mỗi lần dự báo `horizon` ngày chưa thấy.
Khoảng dự báo lấy từ phân vị 10%/90% của sai số backtest (thực nghiệm, không giả định phân phối).
"""

import itertools
import warnings
from datetime import date

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

SEASON = 7
HORIZON = 28
FOLDS = 3
LAGS = (28, 35, 42, 56, 91, 364)
MIN_TRAIN_DAYS = 2 * SEASON + 56


# ------------------------------------------------------------------ thuật toán

def seasonal_naive(y, start, horizon):
    last = np.asarray(y[-SEASON:], dtype=float)
    return np.array([last[i % SEASON] for i in range(horizon)])


def _hw_pass(y, alpha, beta, gamma, phi, season=SEASON):
    level = float(np.mean(y[:season]))
    trend = float((np.mean(y[season:2 * season]) - np.mean(y[:season])) / season)
    seas = list(np.asarray(y[:season], dtype=float) - level)
    sse = 0.0
    for t in range(season, len(y)):
        s = seas[t % season]
        base = level + phi * trend
        err = y[t] - (base + s)
        sse += err * err
        new_level = alpha * (y[t] - s) + (1 - alpha) * base
        trend = beta * (new_level - level) + (1 - beta) * phi * trend
        seas[t % season] = gamma * (y[t] - new_level) + (1 - gamma) * s
        level = new_level
    return sse, level, trend, seas


HW_GRID = list(itertools.product((0.05, 0.15, 0.3), (0.0, 0.02, 0.1), (0.05, 0.15, 0.3)))
HW_PHI = 0.9


def holt_winters(y, start, horizon):
    y = np.asarray(y, dtype=float)
    best = min(
        ((_hw_pass(y, a, b, g, HW_PHI), (a, b, g)) for a, b, g in HW_GRID), key=lambda item: item[0][0]
    )
    (_, level, trend, seas), _params = best
    n = len(y)
    out, damp = [], 0.0
    for i in range(horizon):
        damp += HW_PHI ** (i + 1)
        out.append(level + damp * trend + seas[(n + i) % SEASON])
    return np.maximum(np.array(out), 0.0)


def _gbm_features(y, start, idx):
    """Đặc trưng cho các chỉ số ngày `idx`; chỉ dùng độ trễ >= 28 ngày nên hợp lệ cho mọi horizon <= 28."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    idx = np.asarray(idx)
    ords = start.toordinal() + idx
    days = [date.fromordinal(int(o)) for o in ords]
    dow = np.array([d.weekday() for d in days])
    doy = np.array([d.timetuple().tm_yday for d in days])

    def lagged(lag):
        j = idx - lag
        ok = (j >= 0) & (j < n)
        return np.where(ok, y[np.clip(j, 0, n - 1)], np.nan)

    cols = {
        "dow": dow,
        "month": np.array([d.month for d in days]),
        "dom": np.array([d.day for d in days]),
        "sin_y": np.sin(2 * np.pi * doy / 365.25),
        "cos_y": np.cos(2 * np.pi * doy / 365.25),
        "trend": idx.astype(float),
    }
    for lag in LAGS:
        cols[f"lag{lag}"] = lagged(lag)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # nanmean của lát cắt toàn NaN ở đầu chuỗi
        cols["dow_mean4"] = np.nanmean(np.vstack([lagged(l) for l in (28, 35, 42, 49)]), axis=0)
        cols["roll28"] = np.nanmean(np.vstack([lagged(l) for l in range(28, 56)]), axis=0)
        cols["roll7"] = np.nanmean(np.vstack([lagged(l) for l in range(28, 35)]), axis=0)
    return np.column_stack(list(cols.values()))


def gbm(y, start, horizon):
    y = np.asarray(y, dtype=float)
    n = len(y)
    train_idx = np.arange(56, n)
    model = HistGradientBoostingRegressor(
        max_iter=250, learning_rate=0.06, max_depth=4, min_samples_leaf=15,
        l2_regularization=1.0, random_state=0,
    )
    x_train = _gbm_features(y, start, train_idx)
    keep = ~np.isnan(x_train).all(axis=0)     # ví dụ lag364 khi lịch sử ngắn hơn 1 năm: cột toàn NaN
    model.fit(x_train[:, keep], y[train_idx])
    x_future = _gbm_features(y, start, np.arange(n, n + horizon))
    return np.maximum(model.predict(x_future[:, keep]), 0.0)


ALGORITHMS = {"seasonal_naive": seasonal_naive, "holt_winters": holt_winters, "gbm": gbm}
BASELINE = "seasonal_naive"


# --------------------------------------------------------------------- đánh giá

def backtest(y, start, fn, horizon=HORIZON, folds=FOLDS):
    """Trả về (dự báo, thực tế, số thứ tự fold) nối lại theo thời gian."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    preds, actuals, fold_ids = [], [], []
    for f in range(folds):
        cutoff = n - horizon * (folds - f)
        if cutoff < MIN_TRAIN_DAYS:
            raise ValueError("Chuỗi quá ngắn để backtest")
        preds.append(fn(y[:cutoff], start, horizon))
        actuals.append(y[cutoff:cutoff + horizon])
        fold_ids.append(np.full(horizon, f + 1))
    return np.concatenate(preds), np.concatenate(actuals), np.concatenate(fold_ids)


def metrics(pred, actual, block=SEASON):
    """WAPE/MAPE/RMSE/bias theo ngày và WAPE theo tuần (các khối `block` ngày liên tiếp)."""
    pred, actual = np.asarray(pred, float), np.asarray(actual, float)
    total = actual.sum()
    err = pred - actual
    nz = actual > 0
    blocks = len(actual) // block
    wp = pred[:blocks * block].reshape(blocks, block).sum(axis=1)
    wa = actual[:blocks * block].reshape(blocks, block).sum(axis=1)
    return {
        "wape_daily": float(np.abs(err).sum() / total) if total else float("nan"),
        "mape_daily": float(np.mean(np.abs(err[nz]) / actual[nz])) if nz.any() else float("nan"),
        "rmse_daily": float(np.sqrt(np.mean(err ** 2))),
        "bias": float(err.sum() / total) if total else float("nan"),
        "wape_weekly": float(np.abs(wp - wa).sum() / wa.sum()) if wa.sum() else float("nan"),
    }


def interval_offsets(pred, actual, low=0.1, high=0.9):
    q = np.quantile(np.asarray(actual, float) - np.asarray(pred, float), [low, high])
    return float(q[0]), float(q[1])


def forecast_scope(y, start, horizon=HORIZON, folds=FOLDS, algorithms=None):
    """Backtest + dự báo tương lai cho mọi thuật toán của một chuỗi; chọn champion theo WAPE tuần."""
    algorithms = algorithms or ALGORITHMS
    results = {}
    for name, fn in algorithms.items():
        pred, actual, fold = backtest(y, start, fn, horizon, folds)
        lo, hi = interval_offsets(pred, actual)
        future = fn(y, start, horizon)
        results[name] = {
            "metrics": metrics(pred, actual),
            "backtest": {"pred": pred, "actual": actual, "fold": fold},
            "offsets": (lo, hi),
            "future": future,
            "coverage": float(np.mean((actual >= pred + lo) & (actual <= pred + hi))),
        }
    champion = min(results, key=lambda a: (results[a]["metrics"]["wape_weekly"], results[a]["metrics"]["wape_daily"]))
    return results, champion

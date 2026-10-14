"""Test thuật toán AI/ML thuần (numpy + scikit-learn), không cần cơ sở dữ liệu."""

from datetime import date, timedelta

import numpy as np
import pytest

from insights import anomaly, basket, churn, forecasting, segmentation

START = date(2024, 10, 14)   # một ngày Thứ Hai


def weekly_series(n=500, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    pattern = np.array([1.05, 1.0, 1.0, 1.05, 1.1, 0.8, 0.5])
    y = 100.0 * pattern[np.arange(n) % 7] * (1 + 0.0004 * np.arange(n))
    return y * rng.normal(1.0, noise, n) if noise else y


# ------------------------------------------------------------------ dự báo

def test_seasonal_naive_repeats_last_week():
    y = weekly_series(70)
    forecast = forecasting.seasonal_naive(y, START, 14)
    assert np.allclose(forecast[:7], y[-7:]) and np.allclose(forecast[7:], y[-7:])


def test_holt_winters_recovers_weekly_pattern():
    y = weekly_series(300, noise=0.02)
    forecast = forecasting.holt_winters(y, START, 28)
    actual = weekly_series(328)[300:]
    assert np.abs(forecast - actual).sum() / actual.sum() < 0.10
    assert (forecast >= 0).all()


def test_gbm_handles_missing_long_lags_and_is_non_negative():
    y = weekly_series(120, noise=0.05)          # chưa đủ 364 ngày -> lag364 toàn NaN
    forecast = forecasting.gbm(y, START, 28)
    assert forecast.shape == (28,) and np.isfinite(forecast).all() and (forecast >= 0).all()


def test_gbm_features_never_use_future_values():
    y = np.arange(1.0, 101.0)
    x = forecasting._gbm_features(y, START, np.arange(100, 128))          # 28 ngày tương lai
    lag28_column = 6 + forecasting.LAGS.index(28)    # 6 cột lịch/xu hướng đứng trước các cột độ trễ
    assert x[0, lag28_column] == y[72]               # t=100 -> y[100-28]
    assert x[27, lag28_column] == y[99]              # t=127 -> y[99], không vượt quá dữ liệu đã biết


def test_metrics_values():
    actual = np.array([100.0] * 14)
    pred = np.array([110.0] * 7 + [90.0] * 7)
    m = forecasting.metrics(pred, actual)
    assert m["wape_daily"] == pytest.approx(0.10)
    assert m["bias"] == pytest.approx(0.0)
    assert m["wape_weekly"] == pytest.approx(0.10)          # mỗi tuần lệch 70 trên 700
    assert m["rmse_daily"] == pytest.approx(10.0)


def test_backtest_layout():
    y = weekly_series(300)
    pred, actual, fold = forecasting.backtest(y, START, forecasting.seasonal_naive, horizon=28, folds=3)
    assert len(pred) == len(actual) == 84
    assert list(np.unique(fold)) == [1, 2, 3]
    assert np.allclose(actual, y[-84:])                     # backtest phủ đúng 84 ngày cuối


def test_backtest_rejects_short_series():
    with pytest.raises(ValueError):
        forecasting.backtest(weekly_series(100), START, forecasting.seasonal_naive)


def test_forecast_scope_picks_lowest_weekly_error():
    y = weekly_series(400, noise=0.05)
    results, champion = forecasting.forecast_scope(y, START)
    best = min(results, key=lambda a: results[a]["metrics"]["wape_weekly"])
    assert champion == best
    for res in results.values():
        lo, hi = res["offsets"]
        assert lo < hi and 0.7 <= res["coverage"] <= 0.9      # khoảng 80% phủ ~80% quan sát backtest
        assert len(res["future"]) == forecasting.HORIZON


# ------------------------------------------------------------------ phân khúc

def blobs(seed=0):
    rng = np.random.default_rng(seed)
    big = np.column_stack([rng.integers(1, 15, 60), rng.integers(30, 60, 60), rng.uniform(5e7, 9e7, 60)])
    mid = np.column_stack([rng.integers(30, 90, 60), rng.integers(8, 15, 60), rng.uniform(5e6, 9e6, 60)])
    lost = np.column_stack([rng.integers(250, 360, 60), rng.integers(1, 3, 60), rng.uniform(1e5, 5e5, 60)])
    return np.vstack([big, mid, lost]).astype(float)


def test_kmeans_finds_three_separated_groups_and_names_by_value():
    x = blobs()
    result = segmentation.cluster_customers(x)
    assert result["k"] == 3
    assert sorted(result["names"].values()) == sorted(segmentation.NAMES_BY_K[3])
    top = [i for i in range(len(x)) if result["names"][int(result["labels"][i])] == "Giá trị cao"]
    assert set(top) == set(range(60))                       # đúng nhóm doanh số lớn nhất


def test_rule_segments_and_agreement():
    x = blobs()
    rule = segmentation.rule_segments(x)
    assert len(rule) == len(x) and "VIP" in set(rule)
    ari = segmentation.agreement(segmentation.cluster_customers(x)["labels"], rule)
    assert -1.0 <= ari <= 1.0


# ------------------------------------------------------------------ rời bỏ

def days_of(*offsets, base=date(2025, 1, 1)):
    return np.array([base.toordinal() + o for o in offsets], dtype=np.int64)


def test_snapshot_labels_and_active_filter():
    base = date(2025, 1, 1).toordinal()
    customers = {
        1: (days_of(0, 30, 60, 100, 140), np.full(5, 1000.0)),    # còn mua sau mốc -> nhãn 0
        2: (days_of(0, 30, 60, 90), np.full(4, 1000.0)),          # dừng mua sau 90 -> nhãn 1
        3: (days_of(0), np.array([500.0])),                       # lâu không mua tại mốc -> bị loại
    }
    # Mốc base+200: khách 3 (đơn cuối ngày 0) đã 200 ngày không mua -> không còn "đang hoạt động"
    ids, _, _ = churn.snapshot(customers, base + 200, with_label=False)
    assert 3 not in ids and {1, 2} <= set(ids)
    # Mốc base+100, nhãn nhìn tới base+160: khách 1 có đơn ngày 140 (nhãn 0); khách 2 và 3 không mua nữa (nhãn 1)
    ids2, _, y2 = churn.snapshot(customers, base + 100)
    labels = dict(zip(ids2, y2))
    assert labels == {1: 0, 2: 1, 3: 1}


def test_snapshot_features_only_use_past():
    base = date(2025, 1, 1).toordinal()
    customers = {1: (days_of(0, 10, 20, 150), np.array([1.0, 2.0, 3.0, 1000.0]))}
    _, x, _ = churn.snapshot(customers, base + 30, with_label=False)
    row = dict(zip(churn.FEATURES, x[0]))
    assert row["recency_days"] == 10
    assert row["freq_180"] == 3 and row["monetary_180"] == 6.0           # đơn ngày 150 chưa xảy ra


def test_time_split_has_no_label_leakage():
    rng = np.random.default_rng(0)
    base = date(2024, 1, 1).toordinal()
    customers = {}
    for cid in range(80):
        days = sorted(set(rng.integers(0, 700, rng.integers(5, 40)).tolist()))
        customers[cid] = (np.array(days, dtype=np.int64) + base, rng.uniform(1e5, 1e6, len(days)))
    as_of = base + 699
    first = min(v[0][0] for v in customers.values())
    train_x, train_y, test_x, test_y, test_ids = churn.build_datasets(customers, as_of, first)
    assert len(train_y) > len(test_y) > 0 and set(np.unique(train_y)) <= {0, 1}
    # mốc kiểm tra cách as_of đúng 60 ngày; mốc huấn luyện cuối cùng <= as_of - 120
    ids, _, _ = churn.snapshot(customers, as_of - churn.HORIZON_DAYS)
    assert sorted(ids) == sorted(test_ids)


def test_churn_models_beat_chance_on_signal():
    rng = np.random.default_rng(1)
    n = 800
    recency = rng.uniform(0, 170, n)
    x = np.column_stack([recency] + [rng.normal(size=n) for _ in range(len(churn.FEATURES) - 1)])
    y = (rng.random(n) < 1 / (1 + np.exp(-(recency - 80) / 20))).astype(int)
    results, champion, fitted = churn.evaluate(x[:600], y[:600], x[600:], y[600:])
    assert results[champion]["auc"] > 0.75 and set(fitted) == {"logistic", "gbm"}
    assert results["recency_rule"]["auc"] > 0.75


def test_risk_bands_are_rank_based():
    probs = [0.05, None, 0.5, 0.3, 0.2, 0.1, 0.45, 0.4, 0.35, 0.25, 0.15]       # 10 khách được chấm điểm
    bands = churn.risk_bands(probs)
    assert bands[1] == churn.CHURNED_BAND
    assert [bands[i] for i in (2, 6)] == ["Cao", "Cao"]                         # 20% cao nhất
    assert [bands[i] for i in (7, 8, 3)] == ["Trung bình"] * 3                  # 30% kế tiếp
    assert bands.count("Thấp") == 5
    # mô hình yếu (xác suất đều thấp) vẫn cho ra danh sách cần chăm sóc
    assert churn.risk_bands([0.01 * i for i in range(10)]).count("Cao") == 2
    assert churn.risk_bands([None, None]) == [churn.CHURNED_BAND] * 2


# ------------------------------------------------------------------ bất thường

def test_robust_z_flags_injected_spike_but_not_normal_days():
    y = weekly_series(200, noise=0.03)
    y[180] *= 4
    z, exp = anomaly.robust_z(y)
    assert abs(z[180]) >= anomaly.Z_THRESHOLD
    normal = np.abs(np.nan_to_num(z[100:170]))
    assert (normal < anomaly.Z_THRESHOLD).mean() > 0.9


def test_anomaly_evaluation_finds_most_injected_days():
    result = anomaly.evaluate([weekly_series(400, noise=0.04, seed=s) for s in (1, 2)], seeds=(0, 1))
    assert result["robust_z"]["recall"] > 0.7 and result["robust_z"]["precision"] > 0.7
    assert 0 <= result["isolation_forest"]["f1"] <= 1


def test_inject_marks_requested_number_of_days():
    y = weekly_series(300)
    injected, truth = anomaly.inject(y, np.random.default_rng(0))
    assert truth.sum() == int(anomaly.EVAL_DAYS * anomaly.INJECT_RATE)
    assert not np.allclose(injected[truth], y[truth]) and np.allclose(injected[~truth], y[~truth])


# ------------------------------------------------------------------ giỏ hàng

def test_basket_rules_find_planted_pair():
    rng = np.random.default_rng(0)
    baskets = []
    for _ in range(2000):
        items = {f"S{i}" for i in rng.choice(15, size=rng.integers(1, 4), replace=False)}
        if rng.random() < 0.35:
            items |= {"A", "B"}
        baskets.append(items)
    rules = basket.mine_rules(baskets)
    assert {rules[0]["antecedent"], rules[0]["consequent"]} == {"A", "B"}
    assert rules[0]["lift"] > 2 and 0 < rules[0]["support"] < 1
    assert basket.recovery_of_known_pairs(rules, [("A", "B"), ("X", "Y")]) == 0.5


def test_basket_ignores_rare_pairs():
    assert basket.mine_rules([{"A", "B"}] * 5 + [{"C"}] * 100) == []

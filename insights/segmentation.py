"""Phân khúc khách hàng bằng K-Means trên đặc trưng RFM, so sánh với phân khúc theo luật.

* Đặc trưng (cửa sổ 365 ngày): recency_days, frequency_365, monetary_365 -> log1p -> chuẩn hóa.
* Chọn số nhóm k trong 3..6 theo hệ số silhouette.
* Tên nhóm đặt theo thứ hạng điểm tổng hợp z(M) + z(F) - z(R) của tâm cụm.
* `rule_segments` là phiên bản Python của luật RFM ngũ phân vị (cùng nhãn với view mart_customer_rfm,
  nhưng tính trên cùng cửa sổ 365 ngày) để đo mức độ đồng thuận bằng chỉ số Adjusted Rand.
"""

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.preprocessing import StandardScaler

WINDOW_DAYS = 365
K_RANGE = (3, 4, 5, 6)

NAMES_BY_K = {
    3: ["Giá trị cao", "Phổ thông", "Đã ngừng mua"],
    4: ["Giá trị cao", "Trung thành", "Ít hoạt động", "Đã ngừng mua"],
    5: ["Giá trị cao", "Trung thành", "Phổ thông", "Ít hoạt động", "Đã ngừng mua"],
    6: ["Giá trị cao", "Trung thành", "Phổ thông", "Ít hoạt động", "Nguy cơ rời bỏ", "Đã ngừng mua"],
}


def customer_features(orders, as_of_ordinal):
    """orders: {customer_id: (ordinal_days, revenue)} -> (ids, matrix [recency, frequency, monetary])."""
    ids, rows = [], []
    for customer_id, (days, revenue) in orders.items():
        upto = np.searchsorted(days, as_of_ordinal, side="right")
        if upto == 0:
            continue
        d, r = days[:upto], revenue[:upto]
        in_window = d > as_of_ordinal - WINDOW_DAYS
        ids.append(customer_id)
        rows.append((as_of_ordinal - d[-1], int(in_window.sum()), float(r[in_window].sum())))
    return ids, np.array(rows, dtype=float).reshape(-1, 3)


def _log_scaled(features):
    return StandardScaler().fit_transform(np.log1p(features))


def cluster_customers(features, k_range=K_RANGE, seed=0):
    """Trả về dict: labels, k, silhouette_by_k, names (theo từng nhãn), centers (đơn vị gốc)."""
    x = _log_scaled(features)
    scores, models = {}, {}
    for k in k_range:
        if k >= len(x):
            continue
        model = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(x)
        scores[k] = float(silhouette_score(x, model.labels_))
        models[k] = model
    k = max(scores, key=scores.get)
    labels = models[k].labels_

    centers = np.array([features[labels == c].mean(axis=0) for c in range(k)])      # recency, freq, monetary
    z = (np.log1p(centers) - np.log1p(features).mean(axis=0)) / np.log1p(features).std(axis=0)
    composite = z[:, 2] + z[:, 1] - z[:, 0]
    order = np.argsort(-composite)
    names = {int(cluster): NAMES_BY_K[k][rank] for rank, cluster in enumerate(order)}
    return {
        "labels": labels, "k": k, "silhouette_by_k": scores, "names": names,
        "centers": {names[c]: {"recency_days": float(centers[c, 0]), "frequency_365": float(centers[c, 1]),
                               "monetary_365": float(centers[c, 2]), "customers": int((labels == c).sum())}
                    for c in range(k)},
    }


def _ntile5(values):
    ranks = np.argsort(np.argsort(values, kind="stable"), kind="stable")
    return (ranks * 5 // len(values)) + 1


def rule_segments(features):
    """Luật RFM ngũ phân vị: R cao = mới mua gần đây; cùng điều kiện với view mart_customer_rfm."""
    recency, freq, monetary = features[:, 0], features[:, 1], features[:, 2]
    r, f, m = _ntile5(-recency), _ntile5(freq), _ntile5(monetary)
    out = np.empty(len(features), dtype=object)
    for i in range(len(features)):
        if r[i] >= 4 and f[i] >= 4 and m[i] >= 4:
            out[i] = "VIP"
        elif f[i] >= 4 and r[i] >= 3:
            out[i] = "Trung thành"
        elif r[i] >= 4 and f[i] <= 2:
            out[i] = "Khách mới / tiềm năng"
        elif r[i] <= 2 and m[i] >= 4:
            out[i] = "Nguy cơ rời bỏ (giá trị cao)"
        elif r[i] == 1:
            out[i] = "Đã ngừng mua"
        else:
            out[i] = "Thông thường"
    return out


def agreement(labels, rule_labels):
    return float(adjusted_rand_score(labels, rule_labels))

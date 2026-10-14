"""Phân tích giỏ hàng: tìm cặp sản phẩm hay được mua cùng một đơn (luật kết hợp dạng A -> B).

support    = số đơn chứa cả A và B / tổng số đơn
confidence = số đơn chứa cả A và B / số đơn chứa A
lift       = confidence / (số đơn chứa B / tổng số đơn)     (lift > 1: mua kèm nhiều hơn ngẫu nhiên)
Chỉ giữ cặp có ít nhất MIN_PAIR_ORDERS đơn và lift >= MIN_LIFT.
"""

from collections import Counter
from itertools import combinations

MIN_PAIR_ORDERS = 30
MIN_LIFT = 1.2
TOP_RULES = 40


def mine_rules(baskets, min_pair_orders=MIN_PAIR_ORDERS, min_lift=MIN_LIFT, top=TOP_RULES):
    n = len(baskets)
    item_count, pair_count = Counter(), Counter()
    for basket in baskets:
        item_count.update(basket)
        pair_count.update(combinations(sorted(basket), 2))
    rules = []
    for (a, b), together in pair_count.items():
        if together < min_pair_orders:
            continue
        for ante, cons in ((a, b), (b, a)):
            confidence = together / item_count[ante]
            lift = confidence / (item_count[cons] / n)
            if lift >= min_lift:
                rules.append({"antecedent": ante, "consequent": cons, "pair_orders": together,
                              "support": together / n, "confidence": confidence, "lift": lift})
    rules.sort(key=lambda r: -r["lift"])
    return rules[:top]


def recovery_of_known_pairs(rules, known_pairs, k=None):
    """Tỉ lệ cặp đã biết (mô phỏng cài sẵn) xuất hiện trong `k` luật đầu (mặc định 2 x số cặp)."""
    k = k or 2 * len(known_pairs)
    found = {frozenset((r["antecedent"], r["consequent"])) for r in rules[:k]}
    return sum(frozenset(p) in found for p in known_pairs) / len(known_pairs)

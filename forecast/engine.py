"""Pure demand-forecast and inventory-policy calculations (no database access).

Method
------
level          = 0.6 * avg(last 28 days) + 0.4 * avg(last `window` days)
seasonal ratio = avg(same horizon last year) / avg(28 days before that point last year)
                 (only when there is enough history; clipped to [0.3, 3])
forecast/day   = level * seasonal ratio
safety stock   = z * sigma * sqrt(lead time)          (sigma scaled by seasonal ratio)
ROP            = forecast/day * lead time + safety stock
order-up-to S  = forecast/day * (lead time + review period) + safety stock
suggested qty  = S - (available + on order)   when available + on order <= ROP
"""

import math
from dataclasses import dataclass, field
from datetime import timedelta
from statistics import NormalDist, fmean, pstdev

RECENT_DAYS = 28
RECENT_WEIGHT = 0.6
SEASONAL_CLIP = (0.3, 3.0)


@dataclass
class DemandStats:
    avg_daily: float
    recent_daily: float
    std_daily: float
    seasonal_factor: float
    forecast_daily: float
    weekly: list = field(default_factory=list)
    has_seasonality: bool = False


@dataclass
class Policy:
    safety_stock: int
    reorder_point: int
    order_up_to: int


def _window(daily, start, days):
    return [daily.get(start + timedelta(days=i), 0) for i in range(days)]


def demand_stats(daily, as_of, *, history_start=None, window=90, horizon=14, weeks=12):
    """daily: {date: quantity}. Uses the days strictly before as_of."""
    series = _window(daily, as_of - timedelta(days=window), window)
    recent = series[-RECENT_DAYS:]
    avg_daily = fmean(series)
    recent_daily = fmean(recent)
    level = RECENT_WEIGHT * recent_daily + (1 - RECENT_WEIGHT) * avg_daily
    std_daily = pstdev(series)

    factor, has_season, forecast = 1.0, False, level
    ly_point = as_of - timedelta(days=365)
    if history_start and history_start <= ly_point - timedelta(days=RECENT_DAYS):
        ly_next = fmean(_window(daily, ly_point, horizon))
        ly_ref = fmean(_window(daily, ly_point - timedelta(days=RECENT_DAYS), RECENT_DAYS))
        has_season = True
        if ly_ref > 0:
            factor = min(max(ly_next / ly_ref, SEASONAL_CLIP[0]), SEASONAL_CLIP[1])
            forecast = level * factor
        elif ly_next > 0:  # product starts selling in this season (e.g. Tết goods)
            forecast = max(level, ly_next)
            factor = forecast / level if level else SEASONAL_CLIP[1]

    weekly_start = as_of - timedelta(days=7 * weeks)
    weekly = [sum(_window(daily, weekly_start + timedelta(days=7 * w), 7)) for w in range(weeks)]
    return DemandStats(
        avg_daily=avg_daily, recent_daily=recent_daily, std_daily=std_daily,
        seasonal_factor=factor, forecast_daily=forecast, weekly=weekly, has_seasonality=has_season,
    )


def z_score(service_level):
    return NormalDist().inv_cdf(float(service_level))


def inventory_policy(stats, lead_time_days, review_days, service_level):
    sigma = stats.std_daily * max(stats.seasonal_factor, 0) if stats.has_seasonality else stats.std_daily
    safety = z_score(service_level) * sigma * math.sqrt(max(lead_time_days, 0))
    rop = stats.forecast_daily * lead_time_days + safety
    order_up_to = stats.forecast_daily * (lead_time_days + review_days) + safety
    return Policy(safety_stock=math.ceil(safety), reorder_point=math.ceil(rop),
                  order_up_to=math.ceil(order_up_to))


def suggested_quantity(available, on_order, policy):
    position = available + on_order
    if position > policy.reorder_point:
        return 0
    return max(0, policy.order_up_to - position)


def stockout_days(movements, start, end):
    """Count days in [start, end) whose closing on-hand balance is 0.

    movements: iterable of (date, quantity, balance_after) sorted by time.
    """
    closing = {}
    opening = None
    for day, qty, balance in movements:
        if opening is None:
            opening = balance - qty
        closing[day] = balance
    if opening is None:
        return 0
    count, balance, day = 0, opening, start
    while day < end:
        balance = closing.get(day, balance)
        if balance <= 0:
            count += 1
        day += timedelta(days=1)
    return count

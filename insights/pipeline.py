"""Điều phối huấn luyện / chấm điểm các mô hình và ghi kết quả vào các bảng `insights_*`.

    forecast -> segmentation -> churn -> anomaly -> basket -> insight

Mỗi bài toán chạy trong một transaction riêng và tạo một ModelRun; chạy xong thì ModelRun đó là `is_current`.
Nếu một bài toán lỗi, bản ghi lỗi được lưu (status=failed) và các bài còn lại vẫn chạy.
"""

import logging
import time
from datetime import timedelta

import numpy as np
from django.db import transaction

from . import anomaly as anomaly_mod
from . import basket as basket_mod
from . import churn as churn_mod
from . import data, forecasting, narrative, segmentation
from .models import (
    AIInsight, AnomalyEvent, BasketRule, CustomerScore, ModelMetric, ModelRun, SalesForecast,
)

logger = logging.getLogger(__name__)

TASKS = ["forecast", "segmentation", "churn", "anomaly", "basket", "insight"]
DEPENDS = {"churn": ["segmentation"]}


def _num(x, places=2):
    return round(float(x), places)


class Pipeline:
    def __init__(self, as_of=None, horizon=forecasting.HORIZON, folds=forecasting.FOLDS, use_ai=True,
                 user=None, log=None):
        self.as_of = as_of or data.default_as_of()
        self.horizon, self.folds, self.use_ai, self.user = horizon, folds, use_ai, user
        self.log = log or (lambda msg: None)
        self.ctx = {}
        self.summaries = {}
        self.runs = {}

    # ------------------------------------------------------------------ chung
    def run(self, tasks=None):
        wanted = self._expand(tasks or TASKS)
        for task in wanted:
            self.log(f"==> {ModelRun.Task(task).label}")
            self.runs[task] = self._run_task(task)
            run = self.runs[task]
            if run.status == ModelRun.Status.OK:
                self.log(f"    xong trong {run.duration_s:.1f}s · {run.n_rows:,} dòng · {run.algorithm}")
            else:
                self.log(f"    LỖI: {run.error}")
        return self.runs

    @staticmethod
    def _expand(tasks):
        out = []
        for task in tasks:
            for dep in DEPENDS.get(task, []):
                if dep not in tasks and dep not in out:
                    out.append(dep)
            if task not in out:
                out.append(task)
        return [t for t in TASKS if t in out]

    def _params(self, task):
        base = {"as_of": self.as_of.isoformat()}
        if task == "forecast":
            base.update(horizon=self.horizon, folds=self.folds, algorithms=list(forecasting.ALGORITHMS))
        if task == "churn":
            base.update(horizon_days=churn_mod.HORIZON_DAYS, active_window=churn_mod.ACTIVE_WINDOW,
                        features=churn_mod.FEATURES)
        if task == "segmentation":
            base.update(window_days=segmentation.WINDOW_DAYS, k_range=list(segmentation.K_RANGE))
        if task == "anomaly":
            base.update(z_threshold=anomaly_mod.Z_THRESHOLD, contamination=anomaly_mod.IF_CONTAMINATION)
        if task == "basket":
            base.update(min_pair_orders=basket_mod.MIN_PAIR_ORDERS, min_lift=basket_mod.MIN_LIFT)
        return base

    def _run_task(self, task):
        started = time.monotonic()
        params = self._params(task)
        try:
            with transaction.atomic():
                run = ModelRun.objects.create(task=task, as_of=self.as_of, params=params)
                algorithm, summary, n_rows = getattr(self, f"_{task}")(run)
                run.algorithm, run.summary, run.n_rows = algorithm, summary, n_rows
                run.duration_s = round(time.monotonic() - started, 2)
                run.is_current = True
                run.save()
                ModelRun.objects.filter(task=task, is_current=True).exclude(pk=run.pk).update(is_current=False)
            self.summaries[task] = summary
            self.ctx[f"{task}_run"] = run
            return run
        except Exception as exc:  # noqa: BLE001 - lưu lại lỗi và chạy tiếp bài toán khác
            logger.exception("Pipeline task %s failed", task)
            return ModelRun.objects.create(task=task, as_of=self.as_of, params=params,
                                           status=ModelRun.Status.FAILED, error=f"{type(exc).__name__}: {exc}"[:2000],
                                           duration_s=round(time.monotonic() - started, 2))

    def _orders(self):
        if "orders" not in self.ctx:
            self.ctx["orders"] = data.customer_orders(self.as_of)
        return self.ctx["orders"]

    # ---------------------------------------------------------------- dự báo
    def _forecast(self, run):
        series_list = data.revenue_series(self.as_of)
        if not series_list:
            raise ValueError("Chưa có đơn bán đã giao để dự báo")
        forecasts, metric_rows, champions, total = [], [], {}, {}
        for s in series_list:
            results, champion = forecasting.forecast_scope(s.y, s.start, self.horizon, self.folds)
            champions[s.scope] = champion
            n = len(s.y)
            naive_wape = results[forecasting.BASELINE]["metrics"]["wape_weekly"]
            for algo, res in results.items():
                champ = algo == champion
                met = dict(res["metrics"], coverage_80=res["coverage"],
                           skill_vs_naive_pct=100 * (naive_wape - res["metrics"]["wape_weekly"]) / naive_wape)
                for name, value in met.items():
                    metric_rows.append(ModelMetric(run=run, algorithm=algo, scope=s.scope, split="backtest",
                                                   metric=name, value=value, is_champion=champ))
                lo, hi = res["offsets"]
                bt = res["backtest"]
                first = n - self.horizon * self.folds
                for i, (p, a, f) in enumerate(zip(bt["pred"], bt["actual"], bt["fold"])):
                    forecasts.append(SalesForecast(
                        run=run, scope_type=s.scope_type, scope=s.scope, algorithm=algo, is_champion=champ,
                        date=s.start + timedelta(days=first + i), kind=SalesForecast.Kind.BACKTEST, fold=int(f),
                        actual=_num(a), yhat=_num(p), lower=_num(max(0.0, p + lo)), upper=_num(p + hi)))
                for i, p in enumerate(res["future"]):
                    forecasts.append(SalesForecast(
                        run=run, scope_type=s.scope_type, scope=s.scope, algorithm=algo, is_champion=champ,
                        date=s.start + timedelta(days=n + i), kind=SalesForecast.Kind.FUTURE,
                        yhat=_num(p), lower=_num(max(0.0, p + lo)), upper=_num(p + hi)))
            if s.scope_type == "TOTAL":
                champ_res = results[champion]
                future_sum = float(np.sum(champ_res["future"]))
                last_sum = float(np.sum(s.y[-self.horizon:]))
                total = {
                    "champion": champion,
                    "wape_weekly_pct": _num(100 * champ_res["metrics"]["wape_weekly"], 1),
                    "baseline_wape_weekly_pct": _num(100 * naive_wape, 1),
                    "skill_vs_naive_pct": _num(100 * (naive_wape - champ_res["metrics"]["wape_weekly"]) / naive_wape, 1),
                    "forecast_next_28d_revenue": round(future_sum),
                    "actual_last_28d_revenue": round(last_sum),
                    "change_pct": _num(100 * (future_sum / last_sum - 1), 1) if last_sum else 0.0,
                    "algorithms": {a: _num(100 * r["metrics"]["wape_weekly"], 1) for a, r in results.items()},
                }
        SalesForecast.objects.bulk_create(forecasts, batch_size=2000)
        ModelMetric.objects.bulk_create(metric_rows, batch_size=2000)
        wins = {a: list(champions.values()).count(a) for a in forecasting.ALGORITHMS}
        summary = {"total": total, "champion_by_scope": champions, "champion_wins": wins,
                   "scopes": len(series_list), "history_days": len(series_list[0].y)}
        return max(wins, key=wins.get), summary, len(forecasts)

    # ---------------------------------------------------------- phân khúc
    def _segmentation(self, run):
        orders = self._orders()
        ids, feats = segmentation.customer_features(orders, self.as_of.toordinal())
        if len(ids) < 20:
            raise ValueError("Cần ít nhất 20 khách hàng có đơn để phân khúc")
        result = segmentation.cluster_customers(feats)
        rule = segmentation.rule_segments(feats)
        ari = segmentation.agreement(result["labels"], rule)
        k = result["k"]

        rows = []
        for kk, score in result["silhouette_by_k"].items():
            rows.append(ModelMetric(run=run, algorithm="kmeans", scope=f"k={kk}", split="train",
                                    metric="silhouette", value=score, is_champion=(kk == k)))
        rows.append(ModelMetric(run=run, algorithm="kmeans", scope="ALL", split="train",
                                metric="ari_vs_rule", value=ari, is_champion=True))
        for name, prof in result["centers"].items():
            for metric, value in prof.items():
                rows.append(ModelMetric(run=run, algorithm="kmeans", scope=name, split="profile",
                                        metric=metric, value=value, is_champion=True))
        ModelMetric.objects.bulk_create(rows)
        self.ctx["segments"] = {"ids": ids, "features": feats, "labels": result["labels"],
                                "names": result["names"], "rule": rule}
        summary = {"k": k, "silhouette": _num(result["silhouette_by_k"][k], 3), "ari_vs_rule": _num(ari, 3),
                   "customers": len(ids), "profiles": result["centers"]}
        return f"kmeans(k={k})", summary, len(ids)

    # --------------------------------------------------------------- rời bỏ
    def _churn(self, run):
        orders = self._orders()
        seg = self.ctx["segments"]
        as_ord = self.as_of.toordinal()
        first_ord = int(min(days[0] for days, _ in orders.values()))
        train_x, train_y, test_x, test_y, _ = churn_mod.build_datasets(orders, as_ord, first_ord)
        results, champion, fitted = churn_mod.evaluate(train_x, train_y, test_x, test_y)

        rows = []
        for algo, met in results.items():
            for name, value in met.items():
                rows.append(ModelMetric(run=run, algorithm=algo, scope="ALL", split="test", metric=name,
                                        value=value, is_champion=(algo == champion)))
        for name, value in {"train_rows": len(train_y), "test_rows": len(test_y),
                            "train_churn_rate": float(train_y.mean()), "test_churn_rate": float(test_y.mean())}.items():
            rows.append(ModelMetric(run=run, algorithm="dataset", scope="ALL", split="test", metric=name, value=value))
        imp_model = fitted.get(champion) or fitted["gbm"]
        for feature, value in churn_mod.feature_importance(imp_model, test_x, test_y).items():
            rows.append(ModelMetric(run=run, algorithm=champion if champion in fitted else "gbm", scope=feature,
                                    split="test", metric="importance", value=value, is_champion=True))
        ModelMetric.objects.bulk_create(rows)

        ids, _, prob, drivers = churn_mod.final_scores(orders, as_ord, first_ord, champion)
        prob_by_id = dict(zip(ids, prob.tolist()))
        driver_by_id = dict(zip(ids, drivers))
        bands = churn_mod.risk_bands([prob_by_id.get(cid) for cid in seg["ids"]])
        scores, high, revenue_at_risk, medium = [], 0, 0.0, 0
        for i, cid in enumerate(seg["ids"]):
            recency, freq, money = seg["features"][i]
            p = prob_by_id.get(cid)
            band = bands[i]
            if band == "Cao":
                high += 1
                revenue_at_risk += money
            medium += band == "Trung bình"
            label = int(seg["labels"][i])
            scores.append(CustomerScore(
                run=run, segment_run=self.ctx.get("segmentation_run"), customer_id=cid, as_of=self.as_of,
                recency_days=int(recency), frequency_365=int(freq), monetary_365=_num(money), cluster=label,
                segment=seg["names"][label], rule_segment=str(seg["rule"][i]), churn_prob=p, risk_band=band,
                driver=driver_by_id.get(cid, "")))
        CustomerScore.objects.bulk_create(scores, batch_size=1000)
        summary = {
            "champion": champion, "auc": _num(results[champion]["auc"], 3),
            "baseline_auc": _num(results["recency_rule"]["auc"], 3),
            "precision_top20": _num(results[champion]["precision_top20"], 3),
            "test_churn_rate_pct": _num(100 * test_y.mean(), 1), "scored": len(scores),
            "high_risk": high, "medium_risk": int(medium), "revenue_at_risk_365": round(revenue_at_risk),
            "algorithms": {a: _num(m["auc"], 3) for a, m in results.items()},
        }
        return champion, summary, len(scores)

    # ------------------------------------------------------------ bất thường
    def _anomaly(self, run):
        warehouses = [s for s in data.revenue_series(self.as_of) if s.scope_type == "WAREHOUSE"]
        if not warehouses:
            raise ValueError("Chưa có doanh thu theo kho")
        evaluation = anomaly_mod.evaluate([s.y for s in warehouses])
        champion = max(evaluation, key=lambda a: evaluation[a]["f1"])
        rows = [ModelMetric(run=run, algorithm=algo, scope="ALL", split="injection_eval", metric=m, value=v,
                            is_champion=(algo == champion))
                for algo, met in evaluation.items() for m, v in met.items()]
        ModelMetric.objects.bulk_create(rows)

        events = []
        for s in warehouses:
            detectors, exp, z = anomaly_mod.detect(s.y)
            n = len(s.y)
            for algo, (flag, score) in detectors.items():
                for t in range(n - anomaly_mod.EVAL_DAYS, n):
                    if not flag[t] or np.isnan(exp[t]) or exp[t] <= 0:
                        continue
                    severity = "Cao" if (not np.isnan(z[t]) and abs(z[t]) >= anomaly_mod.Z_HIGH) else "Trung bình"
                    events.append(AnomalyEvent(
                        run=run, date=s.start + timedelta(days=t), scope=s.scope, algorithm=algo,
                        actual=_num(s.y[t]), expected=_num(exp[t]),
                        deviation_pct=_num(100 * (s.y[t] - exp[t]) / exp[t], 1), score=_num(score[t], 3),
                        severity=severity))
        AnomalyEvent.objects.bulk_create(events)
        champion_events = [e for e in events if e.algorithm == champion]
        recent = sorted(champion_events, key=lambda e: -abs(e.deviation_pct))[:3]
        summary = {
            "events": len(champion_events), "high_severity": sum(e.severity == "Cao" for e in champion_events),
            "events_by_algorithm": {a: sum(e.algorithm == a for e in events) for a in evaluation},
            "champion": champion, "f1": {a: _num(m["f1"], 3) for a, m in evaluation.items()},
            "recall": {a: _num(m["recall"], 3) for a, m in evaluation.items()},
            "largest": [{"date": e.date.isoformat(), "scope": e.scope, "deviation_pct": e.deviation_pct}
                        for e in recent],
        }
        return champion, summary, len(events)

    # ----------------------------------------------------------- giỏ hàng
    def _basket(self, run):
        baskets, names = data.order_baskets(self.as_of)
        rules = basket_mod.mine_rules(baskets)
        BasketRule.objects.bulk_create([
            BasketRule(run=run, antecedent_sku=r["antecedent"], antecedent_name=names[r["antecedent"]],
                       consequent_sku=r["consequent"], consequent_name=names[r["consequent"]],
                       pair_orders=r["pair_orders"], support=r["support"], confidence=r["confidence"],
                       lift=r["lift"]) for r in rules])
        extra = [ModelMetric(run=run, algorithm="pair_lift", scope="ALL", split="validation", metric="orders",
                             value=len(baskets), is_champion=True)]
        try:
            from core.management.commands.seed_data import AFFINITY_PAIRS
            extra.append(ModelMetric(run=run, algorithm="pair_lift", scope="ALL", split="validation",
                                     metric="seeded_pairs_recovered",
                                     value=basket_mod.recovery_of_known_pairs(rules, AFFINITY_PAIRS), is_champion=True))
        except ImportError:  # pragma: no cover
            pass
        ModelMetric.objects.bulk_create(extra)
        top = rules[0] if rules else None
        summary = {"orders": len(baskets), "rules": len(rules)}
        if top:
            summary["top_rule"] = {"antecedent_name": names[top["antecedent"]],
                                   "consequent_name": names[top["consequent"]], "lift": _num(top["lift"], 2),
                                   "confidence_pct": _num(100 * top["confidence"], 1)}
        return "pair_lift", summary, len(rules)

    # ----------------------------------------------------------- nhận xét
    def _insight(self, run):
        # Bài toán không chạy trong lần này (vd. `--only insight`) thì lấy kết quả hiện hành cùng ngày as_of
        summaries = dict(self.summaries)
        for task in TASKS[:-1]:
            if not summaries.get(task):
                current = ModelRun.objects.filter(task=task, is_current=True, status="ok",
                                                  as_of=self.as_of).first()
                if current:
                    summaries[task] = current.summary
        if not any(summaries.get(task) for task in TASKS[:-1]):
            raise ValueError(f"Chưa có kết quả mô hình nào cho ngày {self.as_of}; chạy các bài toán khác trước")
        facts = narrative.build_facts(summaries, self.as_of)
        items, used_ai, label = narrative.generate_insights(facts, use_ai=self.use_ai, user=self.user)
        AIInsight.objects.bulk_create([
            AIInsight(run=run, kind=i["kind"], title=i["title"][:200], body=i["body"], generated_by_ai=used_ai,
                      warnings=i["warnings"]) for i in items])
        return label, {"insights": len(items), "generated_by_ai": used_ai,
                       "warnings": sum(len(i["warnings"]) for i in items)}, len(items)


def run_pipeline(tasks=None, **kwargs):
    pipeline = Pipeline(**kwargs)
    return pipeline, pipeline.run(tasks)

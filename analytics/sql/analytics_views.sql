-- =============================================================================
-- Lớp phân tích (analytics layer) cho Apache Superset
-- =============================================================================
-- Superset KHÔNG đọc thẳng bảng nghiệp vụ. Nó chỉ được cấp quyền SELECT trên
-- schema "analytics" này. Các view thuộc sở hữu của user ứng dụng (erp) nên
-- chúng đọc được bảng gốc, trong khi role chỉ-đọc của Superset thì không.
--
-- Nhờ vậy: không lộ auth_user (mật khẩu băm), django_session, và
-- ai_aicalllog.request_excerpt / response_excerpt (nội dung gửi cho AI).
-- Email và điện thoại khách hàng được che bớt ngay trong view.
--
-- File này chạy lại được nhiều lần (idempotent): dùng CREATE OR REPLACE.
-- Áp dụng bằng:  python manage.py create_analytics_views
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS analytics;
COMMENT ON SCHEMA analytics IS 'Lớp dữ liệu chỉ-đọc dành cho Apache Superset';

-- Múi giờ nghiệp vụ: Django lưu timestamptz, KPI của ERP tính theo giờ VN.
-- Mọi view dưới đây quy đổi về ngày địa phương để số liệu khớp với ERP.

-- -----------------------------------------------------------------------------
-- DIMENSIONS
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW analytics.dim_customer AS
SELECT
    c.id                                    AS customer_id,
    c.code                                  AS customer_code,
    c.name                                  AS customer_name,
    -- PII được che: chỉ giữ ký tự đầu và tên miền
    CASE WHEN c.email = '' THEN NULL
         ELSE regexp_replace(c.email, '^(.).*(@.*)$', '\1***\2') END AS email_masked,
    CASE WHEN c.phone = '' THEN NULL
         ELSE '***' || right(regexp_replace(c.phone, '\D', '', 'g'), 3) END AS phone_masked,
    c.is_active,
    (c.created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS created_date
FROM catalog_customer c;

COMMENT ON VIEW analytics.dim_customer IS 'Khách hàng (email/điện thoại đã che)';

CREATE OR REPLACE VIEW analytics.dim_product AS
SELECT
    p.id                 AS product_id,
    p.sku,
    p.name               AS product_name,
    cat.name             AS category,
    p.unit,
    p.cost_price,
    p.sale_price,
    CASE WHEN p.cost_price > 0
         THEN round((p.sale_price - p.cost_price) / p.sale_price * 100, 1) END AS margin_pct,
    s.code               AS supplier_code,
    s.name               AS supplier_name,
    p.reorder_point      AS product_reorder_point,
    p.is_active
FROM catalog_product p
JOIN catalog_category cat ON cat.id = p.category_id
LEFT JOIN catalog_supplier s ON s.id = p.default_supplier_id;

COMMENT ON VIEW analytics.dim_product IS 'Sản phẩm kèm danh mục và nhà cung cấp mặc định';

CREATE OR REPLACE VIEW analytics.dim_warehouse AS
SELECT w.id AS warehouse_id, w.code AS warehouse_code, w.name AS warehouse_name, w.is_active
FROM catalog_warehouse w;

CREATE OR REPLACE VIEW analytics.dim_supplier AS
SELECT
    s.id AS supplier_id, s.code AS supplier_code, s.name AS supplier_name,
    s.lead_time_days, s.rating, s.is_active
FROM catalog_supplier s;

-- -----------------------------------------------------------------------------
-- FACT: dòng đơn bán
-- -----------------------------------------------------------------------------
-- revenue = quantity * unit_price, đúng như cách ERP tính tổng tiền đơn hàng.
-- standard_cogs dùng giá nhập niêm yết của sản phẩm (giá vốn định mức) nên chỉ
-- để so sánh tương đối giữa các sản phẩm; giá vốn thực tế nằm ở
-- mart_daily_sales.cogs (lấy từ sổ kho, khớp với báo cáo của ERP).

CREATE OR REPLACE VIEW analytics.fct_sales_line AS
SELECT
    l.id                                      AS sales_line_id,
    o.id                                      AS sales_order_id,
    o.code                                    AS order_code,
    o.status,
    o.order_date,
    (o.shipped_at  AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS shipped_date,
    (o.confirmed_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS confirmed_date,
    o.shipped_at,
    o.customer_id,
    c.code                                    AS customer_code,
    c.name                                    AS customer_name,
    o.warehouse_id,
    w.code                                    AS warehouse_code,
    l.product_id,
    p.sku,
    p.name                                    AS product_name,
    cat.name                                  AS category,
    l.quantity,
    l.unit_price,
    (l.quantity * l.unit_price)::numeric(18, 2)                     AS revenue,
    (l.quantity * p.cost_price)::numeric(18, 2)                     AS standard_cogs,
    (l.quantity * (l.unit_price - p.cost_price))::numeric(18, 2)    AS standard_gross_profit
FROM sales_salesorderline l
JOIN sales_salesorder   o   ON o.id = l.order_id
JOIN catalog_customer   c   ON c.id = o.customer_id
JOIN catalog_warehouse  w   ON w.id = o.warehouse_id
JOIN catalog_product    p   ON p.id = l.product_id
JOIN catalog_category   cat ON cat.id = p.category_id;

COMMENT ON VIEW analytics.fct_sales_line IS
    'Mỗi dòng = một dòng hàng trên đơn bán. Lọc status = SHIPPED để lấy doanh thu đã thực hiện.';

-- -----------------------------------------------------------------------------
-- FACT: dòng đơn mua
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW analytics.fct_purchase_line AS
SELECT
    l.id                AS purchase_line_id,
    o.id                AS purchase_order_id,
    o.code              AS po_code,
    o.status,
    o.source,
    o.order_date,
    o.expected_date,
    o.received_date,
    o.supplier_id,
    s.code              AS supplier_code,
    s.name              AS supplier_name,
    o.warehouse_id,
    w.code              AS warehouse_code,
    l.product_id,
    p.sku,
    p.name              AS product_name,
    l.quantity          AS ordered_qty,
    l.received_quantity AS received_qty,
    (l.quantity - l.received_quantity)                          AS outstanding_qty,
    l.unit_price,
    (l.quantity * l.unit_price)::numeric(18, 2)                 AS ordered_value,
    (l.received_quantity * l.unit_price)::numeric(18, 2)        AS received_value,
    CASE WHEN o.received_date IS NOT NULL AND o.expected_date IS NOT NULL
         THEN (o.received_date - o.expected_date) END           AS delay_days,
    CASE WHEN o.received_date IS NOT NULL AND o.expected_date IS NOT NULL
         THEN (o.received_date > o.expected_date) END           AS is_late
FROM purchasing_purchaseorderline l
JOIN purchasing_purchaseorder o ON o.id = l.order_id
JOIN catalog_supplier  s ON s.id = o.supplier_id
JOIN catalog_warehouse w ON w.id = o.warehouse_id
JOIN catalog_product   p ON p.id = l.product_id;

-- -----------------------------------------------------------------------------
-- FACT: sổ kho
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW analytics.fct_stock_movement AS
SELECT
    m.id AS movement_id,
    (m.occurred_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS movement_date,
    m.occurred_at,
    m.movement_type,
    m.product_id,
    p.sku,
    p.name            AS product_name,
    cat.name          AS category,
    m.warehouse_id,
    w.code            AS warehouse_code,
    m.quantity,
    m.balance_after,
    m.unit_cost,
    (m.quantity * m.unit_cost)::numeric(18, 2) AS movement_value,
    m.reference
FROM inventory_stockmovement m
JOIN catalog_product   p   ON p.id = m.product_id
JOIN catalog_category  cat ON cat.id = p.category_id
JOIN catalog_warehouse w   ON w.id = m.warehouse_id;

-- -----------------------------------------------------------------------------
-- MART: doanh thu theo ngày × kho (khớp với báo cáo của ERP)
-- -----------------------------------------------------------------------------
-- Doanh thu lấy từ dòng đơn đã giao; giá vốn lấy từ phiếu xuất kho (OUT),
-- đúng công thức reports/kpis.py:sales_summary().

CREATE OR REPLACE VIEW analytics.mart_daily_sales AS
WITH rev AS (
    SELECT shipped_date AS d, warehouse_id, warehouse_code,
           sum(revenue)                  AS revenue,
           sum(quantity)                 AS units,
           count(DISTINCT sales_order_id) AS orders
    FROM analytics.fct_sales_line
    WHERE status = 'SHIPPED' AND shipped_date IS NOT NULL
    GROUP BY 1, 2, 3
),
cogs AS (
    SELECT movement_date AS d, warehouse_id,
           sum(-movement_value) AS cogs
    FROM analytics.fct_stock_movement
    WHERE movement_type = 'OUT'
    GROUP BY 1, 2
)
SELECT
    rev.d                          AS sales_date,
    rev.warehouse_id,
    rev.warehouse_code,
    rev.revenue,
    COALESCE(cogs.cogs, 0)::numeric(18, 2)                      AS cogs,
    (rev.revenue - COALESCE(cogs.cogs, 0))::numeric(18, 2)      AS gross_profit,
    CASE WHEN rev.revenue > 0
         THEN round((rev.revenue - COALESCE(cogs.cogs, 0)) / rev.revenue * 100, 1) END AS gross_margin_pct,
    rev.orders,
    rev.units,
    round(rev.revenue / NULLIF(rev.orders, 0), 0)               AS avg_order_value
FROM rev
LEFT JOIN cogs ON cogs.d = rev.d AND cogs.warehouse_id = rev.warehouse_id;

COMMENT ON VIEW analytics.mart_daily_sales IS
    'Doanh thu / giá vốn / lợi nhuận gộp theo ngày và kho - khớp với reports.kpis.sales_summary()';

-- -----------------------------------------------------------------------------
-- MART CRM: phân khúc khách hàng theo RFM
-- -----------------------------------------------------------------------------
-- R = số ngày kể từ lần mua gần nhất, F = số đơn đã giao, M = tổng doanh thu.
-- Điểm 1-5 chia theo ngũ phân vị (ntile) trên tập khách hàng có phát sinh mua.

CREATE OR REPLACE VIEW analytics.mart_customer_rfm AS
WITH base AS (
    SELECT
        customer_id, customer_code, customer_name,
        max(shipped_date)              AS last_order_date,
        min(shipped_date)              AS first_order_date,
        count(DISTINCT sales_order_id) AS frequency,
        sum(revenue)                   AS monetary,
        sum(quantity)                  AS total_units
    FROM analytics.fct_sales_line
    WHERE status = 'SHIPPED' AND shipped_date IS NOT NULL
    GROUP BY 1, 2, 3
),
scored AS (
    SELECT
        b.*,
        (CURRENT_DATE - b.last_order_date)              AS recency_days,
        ntile(5) OVER (ORDER BY b.last_order_date ASC)  AS r_score,  -- mua gần đây -> điểm cao
        ntile(5) OVER (ORDER BY b.frequency ASC)        AS f_score,
        ntile(5) OVER (ORDER BY b.monetary ASC)         AS m_score
    FROM base b
)
SELECT
    s.customer_id, s.customer_code, s.customer_name,
    c.email_masked, c.phone_masked, c.is_active,
    s.first_order_date, s.last_order_date, s.recency_days,
    s.frequency, s.monetary, s.total_units,
    round(s.monetary / NULLIF(s.frequency, 0), 0) AS avg_order_value,
    s.r_score, s.f_score, s.m_score,
    (s.r_score::text || s.f_score::text || s.m_score::text) AS rfm_cell,
    CASE
        WHEN s.r_score >= 4 AND s.f_score >= 4 AND s.m_score >= 4 THEN 'VIP'
        WHEN s.f_score >= 4 AND s.r_score >= 3                    THEN 'Trung thành'
        WHEN s.r_score >= 4 AND s.f_score <= 2                    THEN 'Khách mới / tiềm năng'
        WHEN s.r_score <= 2 AND s.m_score >= 4                    THEN 'Nguy cơ rời bỏ (giá trị cao)'
        WHEN s.r_score = 1                                         THEN 'Đã ngừng mua'
        ELSE 'Thông thường'
    END AS segment
FROM scored s
JOIN analytics.dim_customer c ON c.customer_id = s.customer_id;

COMMENT ON VIEW analytics.mart_customer_rfm IS
    'Phân khúc khách hàng theo RFM (Recency / Frequency / Monetary)';

-- -----------------------------------------------------------------------------
-- MART: tình trạng tồn kho
-- -----------------------------------------------------------------------------
-- ROP hiệu lực: ưu tiên ROP theo kho do run_forecast tính, nếu NULL thì lấy
-- ROP của sản phẩm (giống property effective_reorder_point trong Django).

CREATE OR REPLACE VIEW analytics.mart_inventory_status AS
SELECT
    sl.product_id,
    p.sku,
    p.product_name,
    p.category,
    p.supplier_code,
    sl.warehouse_id,
    w.warehouse_code,
    sl.on_hand,
    sl.reserved,
    (sl.on_hand - sl.reserved)                                   AS available,
    COALESCE(sl.reorder_point, p.product_reorder_point)          AS reorder_point,
    sl.safety_stock,
    sl.avg_cost,
    (sl.on_hand * sl.avg_cost)::numeric(18, 2)                   AS stock_value,
    CASE
        WHEN (sl.on_hand - sl.reserved) <= 0 THEN 'Hết hàng'
        WHEN (sl.on_hand - sl.reserved) <= COALESCE(sl.reorder_point, p.product_reorder_point)
            THEN 'Sắp hết'
        ELSE 'Đủ hàng'
    END                                                          AS stock_status,
    (sl.updated_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date        AS updated_date
FROM inventory_stocklevel sl
JOIN analytics.dim_product   p ON p.product_id = sl.product_id
JOIN analytics.dim_warehouse w ON w.warehouse_id = sl.warehouse_id;

COMMENT ON VIEW analytics.mart_inventory_status IS
    'Tồn kho hiện tại theo sản phẩm × kho, kèm tình trạng so với điểm đặt hàng lại';

-- -----------------------------------------------------------------------------
-- MART: hiệu quả giao hàng của nhà cung cấp
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW analytics.mart_supplier_performance AS
SELECT
    o.supplier_id,
    s.supplier_code,
    s.supplier_name,
    s.lead_time_days   AS cam_ket_lead_time,
    s.rating,
    count(*)                                              AS po_received,
    count(*) FILTER (WHERE o.received_date > o.expected_date) AS po_late,
    round(100.0 * count(*) FILTER (WHERE o.received_date > o.expected_date)
          / NULLIF(count(*), 0), 1)                       AS late_rate_pct,
    round(avg(o.received_date - o.expected_date), 1)      AS avg_delay_days,
    max(o.received_date - o.expected_date)                AS max_delay_days,
    sum(o.total_amount)::numeric(18, 2)                   AS received_value
FROM purchasing_purchaseorder o
JOIN analytics.dim_supplier s ON s.supplier_id = o.supplier_id
WHERE o.status = 'RECEIVED' AND o.received_date IS NOT NULL AND o.expected_date IS NOT NULL
GROUP BY 1, 2, 3, 4, 5;

-- -----------------------------------------------------------------------------
-- MART: đề xuất nhập hàng do AI xem lại
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW analytics.mart_reorder_suggestion AS
SELECT
    r.id AS suggestion_id,
    (r.created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS created_date,
    r.status,
    p.sku,
    p.product_name,
    p.category,
    w.warehouse_code,
    s.supplier_code,
    r.available,
    r.on_order,
    r.reorder_point,
    r.suggested_qty,
    r.ai_action,
    r.ai_adjusted_qty,
    r.ai_confidence,
    r.ai_flagged,
    r.final_qty,
    CASE WHEN r.ai_adjusted_qty IS NOT NULL AND r.suggested_qty > 0
         THEN round(100.0 * (r.ai_adjusted_qty - r.suggested_qty) / r.suggested_qty, 1) END
        AS ai_adjust_pct
FROM forecast_reordersuggestion r
JOIN analytics.dim_product   p ON p.product_id = r.product_id
JOIN analytics.dim_warehouse w ON w.warehouse_id = r.warehouse_id
JOIN analytics.dim_supplier  s ON s.supplier_id = r.supplier_id;

COMMENT ON VIEW analytics.mart_reorder_suggestion IS
    'Đề xuất nhập hàng: số của hệ thống so với khuyến nghị của AI (có cờ guardrail)';

-- -----------------------------------------------------------------------------
-- MART: chi phí / độ tin cậy của AI
-- -----------------------------------------------------------------------------
-- CHỈ số liệu tổng hợp. Không đưa request_excerpt / response_excerpt vào view
-- vì đó là nội dung đã gửi cho nhà cung cấp AI.

CREATE OR REPLACE VIEW analytics.mart_ai_usage AS
SELECT
    (a.created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS call_date,
    a.feature,
    a.model,
    a.success,
    count(*)                 AS calls,
    sum(a.input_tokens)      AS input_tokens,
    sum(a.output_tokens)     AS output_tokens,
    round(avg(a.latency_ms)) AS avg_latency_ms
FROM ai_aicalllog a
GROUP BY 1, 2, 3, 4;

COMMENT ON VIEW analytics.mart_ai_usage IS
    'Thống kê gọi AI (không chứa nội dung prompt/phản hồi)';

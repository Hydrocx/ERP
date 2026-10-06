# Superset kèm driver PostgreSQL.
# Image chính thức không đóng gói sẵn driver cho nguồn dữ liệu nào, nên phải
# tự cài - đây là cách làm được khuyến nghị trong tài liệu của Superset.
ARG SUPERSET_VERSION=4.1.1
FROM apache/superset:${SUPERSET_VERSION}

USER root
RUN pip install --no-cache-dir \
        "psycopg2-binary==2.9.10" \
        "Pillow==11.0.0"
USER superset

"""Tạo (hoặc cập nhật) tài khoản Superset dành riêng cho MCP server, quyền tối thiểu.

Chạy BÊN TRONG container Superset (file được mount tại /app/create_mcp_user.py):

    docker exec -e MCP_USERNAME=mcp_agent -e MCP_PASSWORD=... erp_superset python /app/create_mcp_user.py

Vai trò: Alpha + sql_lab.
  * KHÔNG phải Admin: không tạo / sửa được user, role, quyền (các công cụ nhóm security của MCP bị từ chối).
  * Có Alpha để tạo / sửa biểu đồ, dashboard, dataset; có sql_lab để chạy truy vấn đọc.
  * CSDL ERP vẫn chỉ-đọc ở tầng Postgres (role superset_ro) và allow_dml=False, nên MCP không ghi được dữ liệu.
Chạy lại nhiều lần an toàn (cập nhật vai trò và mật khẩu của user đã có).
"""

import os

from superset.app import create_app

ROLES = ("Alpha", "sql_lab")

app = create_app()
with app.app_context():
    sm = app.appbuilder.sm
    username, password = os.environ["MCP_USERNAME"], os.environ["MCP_PASSWORD"]
    roles = [sm.find_role(name) for name in ROLES]
    missing = [n for n, r in zip(ROLES, roles) if r is None]
    if missing:
        raise SystemExit(f"Thiếu role: {missing} (chạy `superset init` trước)")

    user = sm.find_user(username=username)
    if user is None:
        sm.add_user(username=username, first_name="MCP", last_name="Agent",
                    email=f"{username}@example.com", role=roles, password=password)
        print(f"Đã tạo user {username} với role {', '.join(ROLES)}")
    else:
        user.roles = roles
        sm.update_user(user)
        sm.reset_password(user.id, password)
        print(f"Đã cập nhật user {username}: role {', '.join(ROLES)} và mật khẩu")

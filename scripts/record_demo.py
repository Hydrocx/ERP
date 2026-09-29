"""Record a narrated (captioned) demo video and screenshots with Playwright.

Usage (from the project root, after seed_data / setup_roles --demo-users / run_forecast):

    pip install playwright imageio-ffmpeg
    python -m playwright install chromium ffmpeg   # or use an installed browser: --channel msedge
    python scripts/record_demo.py --channel msedge

Outputs docs/demo/demo.mp4 and docs/screenshots/*.png.
If the Playwright ffmpeg download is blocked, copy the imageio-ffmpeg binary to
%LOCALAPPDATA%/ms-playwright/ffmpeg-<version>/ffmpeg-win64.exe (the error message shows the path).
NOTE: the demo changes data (approves suggestions, creates/approves/receives a PO, creates a report).
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
VIDEO_DIR = ROOT / "docs" / "demo"
SHOT_DIR = ROOT / "docs" / "screenshots"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
SIZE = {"width": 1366, "height": 768}

CAPTION_JS = """
(text) => {
  let el = document.getElementById('demo-caption');
  if (!el) {
    el = document.createElement('div');
    el.id = 'demo-caption';
    el.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:99999;' +
      'background:rgba(0,0,128,.92);color:#fff;padding:12px 22px;border-radius:10px;font:18px Arial;' +
      'max-width:80%;text-align:center;box-shadow:0 4px 16px rgba(0,0,0,.3)';
    document.body.appendChild(el);
  }
  el.textContent = text;
}
"""


def start_server():
    env = {**os.environ, "PYTHONUTF8": "1"}
    proc = subprocess.Popen([sys.executable, "manage.py", "runserver", f"127.0.0.1:{PORT}", "--noreload"],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"{BASE}/accounts/login/", timeout=2)
            return proc
        except OSError:
            time.sleep(1)
    proc.kill()
    raise SystemExit("Server did not start")


class Demo:
    def __init__(self, page, pause):
        self.page, self.pause = page, pause

    def caption(self, text, wait=None):
        self.page.evaluate(CAPTION_JS, text)
        self.page.wait_for_timeout(int((wait or self.pause) * 1000))

    def go(self, path, text, wait=None):
        self.page.goto(BASE + path)
        self.page.wait_for_load_state("networkidle")
        self.caption(text, wait)

    def shot(self, name, full_page=False):
        self.page.evaluate("() => { const c = document.getElementById('demo-caption'); if (c) c.style.display='none'; }")
        self.page.screenshot(path=str(SHOT_DIR / f"{name}.png"), full_page=full_page)
        self.page.evaluate("() => { const c = document.getElementById('demo-caption'); if (c) c.style.display=''; }")

    def scroll(self, pixels, wait=1.2):
        self.page.mouse.wheel(0, pixels)
        self.page.wait_for_timeout(int(wait * 1000))


def run(demo):
    page = demo.page
    demo.go("/accounts/login/", "Mini ERP trên Wagtail CMS – đăng nhập với vai trò Quản lý")
    page.fill("#id_username", "quanly")
    page.fill("#id_password", "demo12345")
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)  # charts
    demo.caption("Dashboard: doanh thu, lợi nhuận gộp, giá trị tồn, cảnh báo hết hàng / dưới ROP")
    demo.shot("01-dashboard")
    demo.scroll(500)
    demo.caption("Biểu đồ doanh thu 12 tháng, tồn theo kho, top sản phẩm (số liệu do hệ thống tính)")
    demo.shot("01b-dashboard-charts")

    demo.go("/app/inventory/?status=low", "Tồn kho theo kho – lọc các vị trí sắp hết hàng")
    demo.shot("02-inventory")

    demo.go("/app/products/MUT-TET/", "Chi tiết sản phẩm theo mùa: Mứt Tết – doanh số tuần và dự báo")
    page.get_by_role("button", name="Phân tích bằng AI").click()
    page.wait_for_selector("#ai-result .risk, #ai-result .alert", timeout=90000)
    demo.caption("Phân tích AI: mức rủi ro và hành động đề xuất cho từng kho (không có API key thì dùng tóm tắt theo công thức)", 4)
    demo.shot("03-product")

    demo.go("/app/reorder/", "F1 – Đề xuất nhập hàng: ROP / tồn an toàn theo kho, AI xem lại và giải thích")
    page.get_by_role("button", name="Chạy dự báo ngay").click()
    page.wait_for_load_state("networkidle")
    demo.caption("Đã chạy dự báo: mỗi dòng có SL hệ thống, khuyến nghị AI và lý do")
    demo.shot("04-reorder")
    boxes = page.locator("input[name=selected]")
    for i in range(min(3, boxes.count())):
        boxes.nth(i).check()
    demo.caption("Chọn 3 đề xuất → Duyệt → tạo PO nháp (gom theo nhà cung cấp × kho)")
    page.get_by_role("button", name="Duyệt → tạo PO nháp").click()
    page.wait_for_load_state("networkidle")
    demo.caption("PO nháp đã được tạo từ đề xuất AI")

    demo.go("/admin/snippets/purchasing/purchaseorder/?source=AI&status=DRAFT",
            "Wagtail Admin – danh sách PO nháp do AI tạo")
    edit_href = page.locator("table tbody tr td a[href*='/edit/']").first.get_attribute("href")
    page.goto(BASE + edit_href.replace("/edit/", "/inspect/"))
    page.wait_for_load_state("networkidle")
    demo.caption("Trang chi tiết PO: dòng hàng và các nút thao tác theo quyền")
    demo.shot("05-po-inspect")
    page.get_by_role("button", name="Duyệt PO").click()
    page.wait_for_load_state("networkidle")
    demo.caption("Quản lý duyệt PO → Thủ kho nhận hàng")
    page.get_by_role("link", name="Nhận hàng").click()
    page.wait_for_load_state("networkidle")
    demo.caption("Nhận hàng: có thể nhận một phần từng dòng")
    demo.shot("06-po-receive")
    page.get_by_role("button", name="Xác nhận nhập kho").click()
    page.wait_for_load_state("networkidle")
    demo.caption("Đã nhập kho: tồn kho tăng, sổ kho ghi phiếu IN, PO chuyển trạng thái")

    demo.go("/admin/snippets/inventory/stockmovement/", "Lịch sử xuất nhập (sổ kho bất biến, chỉ xem)")
    demo.shot("07-ledger")

    demo.go("/app/reports/", "F2 – Báo cáo AI: hệ thống tính KPI, AI viết nhận xét, Wagtail workflow duyệt")
    page.get_by_role("button", name="Tạo báo cáo").click()
    page.wait_for_load_state("networkidle")
    demo.caption("Báo cáo được tạo dưới dạng trang Wagtail nháp và gửi vào quy trình duyệt")
    demo.shot("08-reports")
    page.locator("a[href*='/admin/pages/']").first.click()
    page.wait_for_load_state("networkidle")
    demo.caption("Người duyệt xem nội dung, cảnh báo kiểm tra số liệu, rồi phê duyệt & xuất bản", 3)
    demo.shot("09-report-edit")

    demo.go("/app/assistant/", "F3 – Trợ lý dữ liệu: AI chỉ lấy số liệu qua các hàm tra cứu có sẵn")
    page.fill(".chat-form input", "Những mặt hàng nào đang hết hàng ở kho HCM?")
    page.get_by_role("button", name="Gửi").click()
    page.wait_for_selector("#chat .msg.bot .source, #chat .msg.error", timeout=90000)
    demo.caption("Trợ lý trả lời bằng số liệu của hệ thống, kèm nguồn dữ liệu (function calling)", 4)
    demo.shot("10-assistant")

    demo.go("/app/invoice/", "F4 – Nhập hóa đơn NCC: AI đọc ảnh hóa đơn, người dùng xác nhận → PO nháp")
    demo.shot("11-invoice")

    demo.go("/admin/", "Trang chủ Wagtail Admin: panel cảnh báo tồn kho và việc cần xử lý")
    demo.shot("12-admin-home")
    demo.go("/admin/snippets/ai/aicalllog/", "Nhật ký gọi AI: token, thời gian, lỗi của từng lần gọi")
    demo.go("/admin/settings/ai/aisettings/", "Cấu hình AI & dự báo trong Wagtail Settings", 3)
    demo.shot("13-ai-settings")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default=None, help="Browser channel, e.g. msedge or chrome")
    parser.add_argument("--pause", type=float, default=2.5, help="Seconds to hold each caption")
    args = parser.parse_args()

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    server = start_server()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel=args.channel, slow_mo=150)
            tmp = VIDEO_DIR / "_raw"
            context = browser.new_context(viewport=SIZE, record_video_dir=str(tmp), record_video_size=SIZE,
                                          locale="vi-VN")
            page = context.new_page()
            page.set_default_timeout(90000)  # AI calls can take a while
            try:
                run(Demo(page, args.pause))
            finally:
                video = page.video
                context.close()
                browser.close()
                if video:
                    shutil.move(video.path(), VIDEO_DIR / "demo.webm")
                shutil.rmtree(tmp, ignore_errors=True)
    finally:
        server.terminate()
    output = to_mp4(VIDEO_DIR / "demo.webm")
    print(f"Video: {output}\nScreenshots: {SHOT_DIR}")


def to_mp4(webm):
    """Convert to H.264 MP4 (plays in Windows / PowerPoint) when imageio-ffmpeg is installed."""
    try:
        import imageio_ffmpeg
    except ImportError:
        return webm
    mp4 = webm.with_suffix(".mp4")
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(webm), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart", str(mp4)], check=True)
    webm.unlink()
    return mp4


if __name__ == "__main__":
    main()

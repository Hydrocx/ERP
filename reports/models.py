from django.db import models
from wagtail import blocks
from wagtail.admin.panels import FieldPanel, FieldRowPanel, MultiFieldPanel
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Page


class KPISummaryBlock(blocks.StaticBlock):
    """Renders the page's stored KPI table (numbers computed by the system, not by AI)."""

    class Meta:
        icon = "table"
        label = "Bảng KPI (hệ thống tính)"
        admin_text = "Hiển thị bảng KPI đã lưu của báo cáo."
        template = "reports/blocks/kpi_summary.html"


class ReportIndexPage(Page):
    intro = RichTextField("Giới thiệu", blank=True)

    content_panels = Page.content_panels + [FieldPanel("intro")]
    subpage_types = ["reports.AIReportPage"]
    max_count = 1

    class Meta:
        verbose_name = "Danh sách báo cáo"

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["reports"] = AIReportPage.objects.child_of(self).live().order_by("-period_end")
        return context


class AIReportPage(Page):
    period_start = models.DateField("Từ ngày")
    period_end = models.DateField("Đến ngày")
    kpi_json = models.JSONField("KPI", default=dict, blank=True)
    body = StreamField(
        [
            ("kpi_summary", KPISummaryBlock()),
            ("heading", blocks.CharBlock(label="Tiêu đề", form_classname="title")),
            ("paragraph", blocks.RichTextBlock(label="Đoạn văn")),
            ("bullets", blocks.ListBlock(blocks.CharBlock(label="Ý"), label="Danh sách")),
        ],
        blank=True,
        verbose_name="Nội dung",
    )
    generated_by_ai = models.BooleanField("Do AI viết", default=False)
    ai_warnings = models.JSONField("Cảnh báo kiểm tra số liệu", default=list, blank=True)

    content_panels = Page.content_panels + [
        FieldRowPanel([FieldPanel("period_start"), FieldPanel("period_end")]),
        FieldPanel("body"),
        MultiFieldPanel(
            [FieldPanel("generated_by_ai", read_only=True), FieldPanel("ai_warnings", read_only=True)],
            heading="Kiểm soát AI",
        ),
    ]
    parent_page_types = ["reports.ReportIndexPage"]
    subpage_types = []

    class Meta:
        verbose_name = "Báo cáo AI"
        verbose_name_plural = "Báo cáo AI"

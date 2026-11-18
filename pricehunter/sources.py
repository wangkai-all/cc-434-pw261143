"""来源定义与注册。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    name: str
    base_url: str
    start_path: str
    parser_key: str
    parser_version: str
    promo_threshold: float = 0.10   # 折扣 >= 10% 记促销命中
    anomaly_z: float = 2.0
    page_kind: str = "html"          # html | json


def default_sources(base_url: str) -> list[SourceSpec]:
    return [
        SourceSpec("acme", "Acme 商城（HTML 分页）", base_url,
                   "/acme/products?page=1", "acme_html", "acme-parser-v1"),
        SourceSpec("bargain", "Bargain 接口（JSON 游标分页）", base_url,
                   "/bargain/api/items", "bargain_json", "bargain-parser-v1",
                   page_kind="json"),
        SourceSpec("ceg", "CegMart（维护频发）", base_url,
                   "/ceg/catalog/page/1", "ceg_html", "ceg-parser-v1"),
    ]


def as_dict(spec: SourceSpec) -> dict:
    return {
        "source_id": spec.source_id,
        "name": spec.name,
        "base_url": spec.base_url,
        "parser_key": spec.parser_key,
        "parser_version": spec.parser_version,
        "promo_threshold": spec.promo_threshold,
        "anomaly_z": spec.anomaly_z,
    }

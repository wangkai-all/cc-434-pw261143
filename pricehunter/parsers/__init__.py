"""版本化解析器。

约定：
- 每个 parser_key 可有多个版本；SOURCE_REGISTRY 声明来源当前使用的版本。
- parse() 只负责结构解析，不做存储；遇到结构不匹配抛 StructureChanged。
- 动态字段（list_price / promo）缺失时返回 None，不能因为新增字段而失败。
- 解析器必须能区分「合法空页」（列表容器存在、零个商品）与「结构变化」。
"""
from __future__ import annotations

import json

from ..minisoup import parse_html
from ..model import ErrorKind, ParsedItem, ParseResult


class StructureChanged(Exception):
    """页面结构与解析器预期不符，需要新版本解析器。"""

    def __init__(self, message: str, *, kind: ErrorKind = ErrorKind.STRUCTURE_CHANGED) -> None:
        super().__init__(message)
        self.kind = kind


def _parse_money(text: str) -> int:
    """'$12.99' -> 1299，支持整数美元。"""
    cleaned = text.strip().replace(",", "")
    if cleaned.startswith("$"):
        cleaned = cleaned[1:]
    value = float(cleaned)
    return round(value * 100)


# ---------- acme ----------
class AcmeParserV1:
    key = "acme_html"
    version = "acme-parser-v1"
    handles = ("text/html",)

    def parse(self, body: bytes) -> ParseResult:
        root = parse_html(body.decode("utf-8"))
        container = root.find(tag="div", cls="product-list") or root.find(tag="div", attr="id", attr_value="product-list")
        if container is None:
            # v2 页面没有旧容器：明确的结构变化，而不是空结果
            if root.find(tag="section", attr="id", attr_value="listing") is not None:
                raise StructureChanged(
                    "acme 页面已升级为 v2 卡片结构（section#listing），v1 解析器不适用")
            raise StructureChanged("acme 页面缺少 div#product-list 容器")
        products = container.select(tag="div", cls="product")
        items: list[ParsedItem] = []
        for node in products:
            sku = node.attrs.get("data-sku")
            name_node = node.find(tag="h2", cls="name")
            price_node = node.find(tag="span", cls="price")
            ccy_node = node.find(tag="span", cls="currency")
            if not sku or name_node is None or price_node is None:
                raise StructureChanged(
                    f"商品节点缺少必需字段: sku={sku}", kind=ErrorKind.FIELD_MISSING)
            items.append(ParsedItem(
                sku=sku,
                name=name_node.get_text(),
                price_cents=_parse_money(price_node.get_text()),
                currency=(ccy_node.get_text() if ccy_node else "USD"),
            ))
        next_link = root.find(tag="a", cls="next")
        return ParseResult(items, next_link.attrs.get("href") if next_link else None)


class AcmeParserV2:
    key = "acme_html"
    version = "acme-parser-v2"
    handles = ("text/html",)

    def parse(self, body: bytes) -> ParseResult:
        root = parse_html(body.decode("utf-8"))
        section = root.find(tag="section", attr="id", attr_value="listing")
        if section is None:
            if root.find(tag="div", attr="id", attr_value="product-list") is not None:
                raise StructureChanged(
                    "该快照为旧版 v1 结构（div#product-list），请改用 v1 解析器")
            raise StructureChanged("acme 页面缺少 section#listing 容器")
        items: list[ParsedItem] = []
        for card in section.select(tag="article", cls="card"):
            sku = card.attrs.get("data-sku")
            if not sku or "data-price-cents" not in card.attrs:
                raise StructureChanged(
                    f"卡片缺少 data-sku / data-price-cents: {card.attrs}",
                    kind=ErrorKind.FIELD_MISSING)
            title = card.find(tag="h3")
            list_price = card.attrs.get("data-list-price-cents")
            items.append(ParsedItem(
                sku=sku,
                name=title.get_text() if title else sku,
                price_cents=int(card.attrs["data-price-cents"]),
                currency=card.attrs.get("data-currency", "USD"),
                list_price_cents=int(list_price) if list_price else None,
            ))
        next_link = root.find(tag="a", attr="rel", attr_value="next")
        return ParseResult(items, next_link.attrs.get("href") if next_link else None)


# ---------- bargain（JSON，游标分页；动态字段向后兼容） ----------
class BargainParserV1:
    key = "bargain_json"
    version = "bargain-parser-v1"
    handles = ("application/json",)

    def parse(self, body: bytes) -> ParseResult:
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise StructureChanged(f"JSON 解析失败: {exc}",
                                   kind=ErrorKind.PARSE_ERROR) from exc
        if not isinstance(payload, dict) or "items" not in payload:
            raise StructureChanged("响应缺少 items 数组（接口结构变化）")
        items: list[ParsedItem] = []
        for raw in payload["items"]:
            if "sku" not in raw or "price" not in raw:
                raise StructureChanged(
                    f"商品记录缺少 sku/price 字段: {sorted(raw.keys())}",
                    kind=ErrorKind.FIELD_MISSING)
            # 动态字段 list_price / promo 是 D3 之后新增的：识别就带上，没有就 None
            list_price = _parse_money(raw["list_price"]) if raw.get("list_price") else None
            items.append(ParsedItem(
                sku=raw["sku"],
                name=raw.get("name", raw["sku"]),
                price_cents=_parse_money(raw["price"]),
                currency=raw.get("currency", "USD"),
                list_price_cents=list_price,
                promo_code=raw.get("promo"),
                raw=raw,
            ))
        cursor = payload.get("nextCursor")
        next_url = f"/bargain/api/items?cursor={cursor}" if cursor else None
        return ParseResult(items, next_url)


# ---------- ceg ----------
class CegParserV1:
    key = "ceg_html"
    version = "ceg-parser-v1"
    handles = ("text/html",)

    def parse(self, body: bytes) -> ParseResult:
        root = parse_html(body.decode("utf-8"))
        catalog = root.find(tag="ul", cls="catalog")
        if catalog is None:
            raise StructureChanged("ceg 页面缺少 ul.catalog 容器")
        items: list[ParsedItem] = []
        for node in catalog.select(tag="li", cls="item"):
            sku = node.attrs.get("data-sku")
            title = node.find(tag="p", cls="title")
            money = node.find(tag="span", cls="money")
            ccy = node.find(tag="span", cls="ccy")
            if not sku or title is None or money is None:
                raise StructureChanged(
                    f"商品节点缺少必需字段: sku={sku}", kind=ErrorKind.FIELD_MISSING)
            items.append(ParsedItem(
                sku=sku,
                name=title.get_text(),
                price_cents=_parse_money(money.get_text()),
                currency=ccy.get_text() if ccy else "USD",
            ))
        pager = root.find(tag="a", cls="pager")
        return ParseResult(items, pager.attrs.get("href") if pager else None)


# 注册表：key -> {版本号 -> 实例}
PARSER_REGISTRY: dict[str, dict[str, object]] = {
    "acme_html": {
        AcmeParserV1.version: AcmeParserV1(),
        AcmeParserV2.version: AcmeParserV2(),
    },
    "bargain_json": {BargainParserV1.version: BargainParserV1()},
    "ceg_html": {CegParserV1.version: CegParserV1()},
}


def get_parser(parser_key: str, version: str):
    try:
        return PARSER_REGISTRY[parser_key][version]
    except KeyError as exc:
        raise KeyError(f"未注册的解析器: {parser_key}@{version}") from exc

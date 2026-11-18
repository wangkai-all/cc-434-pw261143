"""夹具数据与场景定义（确定性，无随机）。

时间线（模拟日期 2026-11-13 ~ 2026-11-18）：
- 11-13 D1：三站全部正常。
- 11-14 D2：bargain 前两次请求返回 429，重试后成功；acme 的 SKU-1001 出现错误价 499。
- 11-15 D3：cegMart 返回 200 软维护页（绝不能被当成空结果）；
            bargain 响应新增动态字段 list_price / promo。
- 11-16 D4：acme 页面结构升级为 v2 标记（旧解析器应报 structure_changed）；
            cegMart 返回 503 硬维护。
- 11-17 D5：三站恢复正常；bargain 的 SKU-2003 出现价格尖峰（异常告警）。
- 11-18 D6：正常；acme 提供 11-14 的归档修正快照（499 -> 1250），用于回溯修订。
"""
from __future__ import annotations

# ---- 通用 ----
CURRENCY = "USD"

# acme（HTML 站，D4 起结构从 v1 切换到 v2）
ACME = {
    "source_id": "acme",
    "pages": [
        [
            ["SKU-1001", "Acme 无线鼠标"],
            ["SKU-1002", "Acme 机械键盘"],
            ["SKU-1003", "Acme 27 寸显示器"],
        ],
        [
            ["SKU-1004", "Acme 高清摄像头"],
            ["SKU-1005", "Acme USB-C 扩展坞"],
        ],
    ],
    # 价格表：sku -> {日期: (price, list_price)}
    "prices": {
        "SKU-1001": {
            "2026-11-13": (1299, None),
            "2026-11-14": (499, None),    # 错误价，后续归档修正为 1250
            "2026-11-15": (1299, None),
            "2026-11-16": (1049, 1299),  # v2 结构 + 促销价
            "2026-11-17": (1049, 1299),
            "2026-11-18": (1049, 1299),
        },
        "SKU-1002": {
            "2026-11-13": (4599, None), "2026-11-14": (4599, None),
            "2026-11-15": (4499, None), "2026-11-16": (4499, 4599),
            "2026-11-17": (4399, 4599), "2026-11-18": (4399, 4599),
        },
        "SKU-1003": {
            "2026-11-13": (15999, None), "2026-11-14": (15999, None),
            "2026-11-15": (15999, None), "2026-11-16": (14999, 15999),
            "2026-11-17": (14999, 15999), "2026-11-18": (14999, 15999),
        },
        "SKU-1004": {
            "2026-11-13": (2199, None), "2026-11-14": (2199, None),
            "2026-11-15": (2199, None), "2026-11-16": (2199, None),
            "2026-11-17": (2099, 2199), "2026-11-18": (2099, 2199),
        },
        "SKU-1005": {
            "2026-11-13": (1899, None), "2026-11-14": (1899, None),
            "2026-11-15": (1899, None), "2026-11-16": (1899, None),
            "2026-11-17": (1899, None), "2026-11-18": (1799, 1899),
        },
    },
    "archive": {  # 历史修正快照（v2 结构）
        "2026-11-14": {"SKU-1001": (1250, None)},
    },
}

# bargain（JSON API，游标分页；D3 起增加动态字段）
BARGAIN = {
    "source_id": "bargain",
    "pages": [
        [
            ["SKU-2001", "Bargain 蓝牙耳机"],
            ["SKU-2002", "Bargain 扫地机器人"],
            ["SKU-2003", "Bargain 智能台灯"],
        ],
        [
            ["SKU-2004", "Bargain 电动牙刷"],
            ["SKU-2005", "Bargain 空气炸锅"],
            ["SKU-2006", "Bargain 便携充电宝"],
        ],
    ],
    "prices": {
        "SKU-2001": {"all": (2599, None)},
        "SKU-2002": {"all": (4599, None)},
        "SKU-2003": {  # D5 尖峰
            "2026-11-13": (899, None), "2026-11-14": (899, None),
            "2026-11-15": (899, None), "2026-11-16": (899, None),
            "2026-11-17": (2599, None), "2026-11-18": (899, None),
        },
        "SKU-2004": {"all": (1599, None)},
        "SKU-2005": {
            "2026-11-13": (3299, None), "2026-11-14": (3199, None),
            "2026-11-15": (3299, None), "2026-11-16": (3099, None),
            "2026-11-17": (3199, None), "2026-11-18": (3299, None),
        },
        "SKU-2006": {"all": (599, None)},
    },
    # D3 起出现的动态字段：list_price 与 promo 码
    "dynamic_from": "2026-11-15",
    "list_prices": {
        "SKU-2001": (3199, "SAVE15"),
        "SKU-2002": (5999, "DEAL20"),
        "SKU-2006": (799, "SAVE15"),
    },
    "rate_limit_day": "2026-11-14",
    "rate_limit_hits": 2,  # 当天前 N 次请求返回 429
}

# cegMart（维护频发的 HTML 站）
CEG = {
    "source_id": "ceg",
    "pages": [
        [
            ["SKU-3001", "CegMart 电饭煲"],
            ["SKU-3002", "CegMart 破壁机"],
        ],
        [
            ["SKU-3003", "CegMart 电水壶"],
            ["SKU-3004", "CegMart 三明治机"],
        ],
    ],
    "prices": {
        "SKU-3001": {"all": (1999, None)},
        "SKU-3002": {
            "2026-11-13": (2499, None), "2026-11-14": (2499, None),
            "2026-11-15": (2499, None),
            "2026-11-16": (2499, None),  # 维护，实际不会被采集
            "2026-11-17": (2399, None), "2026-11-18": (2399, None),
        },
        "SKU-3003": {"all": (799, None)},
        "SKU-3004": {"all": (1299, None)},
    },
    "soft_maintenance_day": "2026-11-15",
    "hard_maintenance_day": "2026-11-16",
}

SCENARIO_DAYS = [
    "2026-11-13", "2026-11-14", "2026-11-15",
    "2026-11-16", "2026-11-17", "2026-11-18",
]
STRUCTURE_V2_DAY = "2026-11-16"


def price_of(table: dict, sku: str, day: str) -> tuple[int, None]:
    entry = table[sku]
    if "all" in entry:
        return entry["all"]
    return entry[day]


# ---------- HTML 生成 ----------
def _money(cents: int) -> str:
    return f"${cents / 100:.2f}"


def acme_html(day: str, page_no: int, archive_date: str | None = None) -> str:
    """acme 页面。archive_date 不为 None 时生成归档修正快照（始终 v2 结构）。"""
    effective_day = archive_date or day
    overrides = ACME["archive"].get(effective_day, {}) if archive_date else {}
    v2 = archive_date is not None or day >= STRUCTURE_V2_DAY

    if page_no > len(ACME["pages"]):
        # 合法空页：列表容器仍在，只是没有商品节点
        if v2:
            return ('<!doctype html><html><head><title>Acme Mall</title></head>'
                    '<body><section id="listing" data-empty="true"></section></body></html>')
        return ('<!doctype html><html><head><title>Acme Mall</title></head>'
                '<body><div id="product-list" data-empty="true"></div></body></html>')

    rows = ACME["pages"][page_no - 1]
    chunks = []
    if not v2:
        for sku, name in rows:
            price, _ = overrides.get(sku) or price_of(ACME["prices"], sku, effective_day)
            chunks.append(
                f'<div class="product" data-sku="{sku}">'
                f'<h2 class="name">{name}</h2>'
                f'<span class="price">{_money(price)}</span>'
                f'<span class="currency">{CURRENCY}</span></div>'
            )
        next_link = (f'<a class="next" href="/acme/products?page={page_no + 1}">下一页</a>'
                     if page_no < len(ACME["pages"]) else "")
        body = f'<div id="product-list">{"".join(chunks)}</div>{next_link}'
    else:
        for sku, name in rows:
            price, list_price = overrides.get(sku) or price_of(
                ACME["prices"], sku, effective_day)
            list_attr = f' data-list-price-cents="{list_price}"' if list_price else ""
            chunks.append(
                f'<article class="card" data-sku="{sku}" data-price-cents="{price}" '
                f'data-currency="{CURRENCY}"{list_attr}><h3>{name}</h3></article>'
            )
        next_link = (f'<a rel="next" href="/acme/products?page={page_no + 1}">下一页</a>'
                     if page_no < len(ACME["pages"]) else "")
        body = f'<section id="listing">{"".join(chunks)}</section>{next_link}'
    return f'<!doctype html><html><head><title>Acme Mall</title></head><body>{body}</body></html>'


def ceg_html(day: str, page_no: int) -> str:
    if page_no > len(CEG["pages"]):
        return ('<!doctype html><html><head><title>CegMart</title></head>'
                '<body><ul class="catalog" data-empty="true"></ul></body></html>')
    rows = CEG["pages"][page_no - 1]
    items = []
    for sku, name in rows:
        price, _ = price_of(CEG["prices"], sku, day)
        items.append(
            f'<li class="item" data-sku="{sku}"><p class="title">{name}</p>'
            f'<span class="money">{_money(price)}</span>'
            f'<span class="ccy">{CURRENCY}</span></li>'
        )
    next_link = (f'<a class="pager" href="/ceg/catalog/page/{page_no + 1}">next</a>'
                 if page_no < len(CEG["pages"]) else "")
    return ('<!doctype html><html><head><title>CegMart</title></head><body>'
            f'<ul class="catalog">{"".join(items)}</ul>{next_link}</body></html>')


def soft_maintenance_html(site: str) -> str:
    """200 软维护页：带显式维护标记，与正常空页严格区分。"""
    return (
        '<!doctype html><html><head>'
        '<meta name="page-state" content="maintenance">'
        f'<title>{site} - Scheduled Maintenance</title></head><body>'
        '<div data-page="maintenance">'
        '<h1>系统维护中</h1><p>本站点正在进行计划维护，请稍后再试。</p>'
        '</div></body></html>'
    )


def bargain_json(day: str, cursor: str | None) -> dict:
    page_idx = 0 if cursor in (None, "", "start") else int(cursor.lstrip("c")) - 1
    is_last = page_idx >= len(BARGAIN["pages"]) - 1
    items = []
    for sku, name in BARGAIN["pages"][page_idx]:
        price, _ = price_of(BARGAIN["prices"], sku, day)
        item = {
            "sku": sku,
            "name": name,
            "price": _money(price),
            "currency": CURRENCY,
        }
        # 动态字段：D3 起部分商品出现 list_price / promo，旧解析器必须容忍其存在
        if day >= BARGAIN["dynamic_from"] and sku in BARGAIN["list_prices"]:
            list_price, promo = BARGAIN["list_prices"][sku]
            item["list_price"] = _money(list_price)
            item["promo"] = promo
        items.append(item)
    return {
        "items": items,
        "nextCursor": None if is_last else f"c{page_idx + 2}",
    }

"""基于 html.parser 的极简 DOM 工具，只服务于本项目受控的夹具 HTML。"""
from __future__ import annotations

from html.parser import HTMLParser

_VOID = {"meta", "link", "img", "br", "hr", "input", "area", "base", "col", "embed", "source", "track", "wbr"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "Node | None") -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[Node] = []
        self.parent = parent
        # tag is None 表示纯文本节点
        self.text: str | None = None

    # ---- 查询 ----
    def select(
        self,
        tag: str | None = None,
        cls: str | None = None,
        attr: str | None = None,
        attr_value: str | None = None,
    ) -> list["Node"]:
        out: list[Node] = []
        self._collect(tag, cls, attr, attr_value, out)
        return out

    def _collect(self, tag, cls, attr, attr_value, out: list["Node"]) -> None:
        for child in self.children:
            if child.tag is not None:
                if (
                    (tag is None or child.tag == tag)
                    and (cls is None or cls in child.attrs.get("class", "").split())
                    and (attr is None or attr in child.attrs)
                    and (attr_value is None or child.attrs.get(attr) == attr_value)
                ):
                    out.append(child)
                child._collect(tag, cls, attr, attr_value, out)

    def get_text(self) -> str:
        if self.text is not None:
            return self.text
        return "".join(c.get_text() for c in self.children).strip()

    def find(self, **kwargs) -> "Node | None":
        hits = self.select(**kwargs)
        return hits[0] if hits else None

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"<Node {self.tag} {self.attrs}>"


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.stack: list[Node] = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        # 容错：向上找到最近的同名标签弹出
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not data.strip():
            return
        node = Node(None, {}, self.stack[-1])
        node.text = data
        self.stack[-1].children.append(node)


def parse_html(html: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(html)
    return builder.root

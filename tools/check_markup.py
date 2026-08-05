"""Markup validity checker for ui/index.html."""
import sys
from pathlib import Path
from html.parser import HTMLParser


class MarkupChecker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.errors = []
        self.stack = []  # list of (tag, attrs_dict)
        self.div_open_count = 0
        self.div_close_count = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "div":
            self.div_open_count += 1

        if tag in ("meta", "link", "input", "img", "br", "hr", "source", "area", "base", "col", "embed", "param", "track", "wbr"):
            return

        self.stack.append((tag, attrs_dict))

    def handle_endtag(self, tag):
        if tag in ("meta", "link", "input", "img", "br", "hr", "source", "area", "base", "col", "embed", "param", "track", "wbr"):
            return

        if tag == "div":
            self.div_close_count += 1

        if not self.stack:
            self.errors.append(f"Unexpected closing tag </{tag}> with empty stack")
            return

        idx = -1
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                idx = i
                break

        if idx == -1:
            self.errors.append(f"Unmatched closing tag </{tag}>")
            return

        element_tag, element_attrs = self.stack[idx]
        element_id = element_attrs.get("id", "")
        element_classes = element_attrs.get("class", "").split()

        is_target = (
            "modal-scrim" in element_classes
            or (element_id and element_id.endswith("View"))
            or element_id in ("ingestPanel", "splitLayout")
        )

        if is_target:
            for ancestor_tag, ancestor_attrs in self.stack[:idx]:
                ancestor_classes = ancestor_attrs.get("class", "").split()
                if "hidden" in ancestor_classes:
                    anc_id = ancestor_attrs.get("id", ancestor_tag)
                    self.errors.append(
                        f"Target element <{element_tag} id='{element_id}' class='{' '.join(element_classes)}'> "
                        f"has an ancestor carrying 'hidden': <{ancestor_tag} id='{anc_id}'>"
                    )

        self.stack = self.stack[:idx]


def check(html_path: str) -> list[str]:
    path = Path(html_path)
    if not path.exists():
        return [f"File not found: {html_path}"]

    content = path.read_text(encoding="utf-8")
    parser = MarkupChecker()
    parser.feed(content)

    errors = list(parser.errors)
    if parser.div_open_count != parser.div_close_count:
        errors.append(f"div open count ({parser.div_open_count}) != close count ({parser.div_close_count})")

    if parser.stack:
        unclosed = [f"<{t[0]} id='{t[1].get('id', '')}'>" for t in parser.stack]
        errors.append(f"Unclosed tags at EOF: {unclosed}")

    return errors


def main():
    repo_root = Path(__file__).resolve().parent.parent
    index_html = repo_root / "ui" / "index.html"
    errors = check(str(index_html))
    if errors:
        print(f"FAIL: {len(errors)} markup error(s) found in {index_html}:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print("OK")
        sys.exit(0)


if __name__ == "__main__":
    main()

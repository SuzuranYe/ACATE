"""Checks on the specification documents themselves: heading numbers, 目录, 返回顶部, links, the version and the images."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "Spec"
HISTORY = "ACATE_Spec_Version_Evolution.md"
OWN_FORMAT = {"ACATE_Spec_Glossary.md"}


def formal_documents() -> list[Path]:
    return sorted(p for p in SPEC.glob("ACATE_Spec_*.md") if p.name != HISTORY)


def without_code(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", text)


def heading_slug(heading: str) -> str:
    slug = re.sub(r"[^\w\- 一-鿿]", "", heading.strip().lower())
    return slug.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found = set(re.findall(r'<a id="([^"]+)"', without_code(text)))
    found |= {heading_slug(h) for h in re.findall(r"^#{1,6} (.+)$", text, re.M)}
    return found


class SpecTests(unittest.TestCase):
    def test_headings_are_numbered_with_anchors_and_back_links(self) -> None:
        for path in formal_documents():
            if path.name in OWN_FORMAT:
                continue
            with self.subTest(path.name):
                body = without_code(path.read_text(encoding="utf-8"))
                heads = re.findall(r'<a id="(s[\d-]+)"></a>\n(#{2,3}) §([\d.]+) (.+)', body)
                plain = [h for h in re.findall(r"^#{2,3} (.+)$", body, re.M) if h != "目录" and not h.startswith("§")]
                self.assertEqual([], plain, "每个章、节标题都带 § 编号和锚点")
                part = sub = 0
                for anchor, level, number, _ in heads:
                    if level == "##":
                        part, sub = part + 1, 0
                        expected = f"{part}"
                    else:
                        sub += 1
                        expected = f"{part}.{sub}"
                    self.assertEqual(expected, number, f"编号连续：{number}")
                    self.assertEqual("s" + expected.replace(".", "-"), anchor)
                self.assertEqual(len(heads), body.count("[返回顶部](#toc)"), "每个标题下有返回顶部")
                toc = body.split("## 目录", 1)[1].split('<a id="s1">', 1)[0]
                listed = re.findall(r"\[(§[\d.]+ [^\]]+)\]\(#(s[\d-]+)\)", toc)
                self.assertEqual([(f"§{n} {t.strip()}", a) for a, _, n, t in heads], listed, "目录与标题一致")

    def test_first_line_is_the_file_name_with_a_chinese_subtitle(self) -> None:
        for path in list(SPEC.glob("*.md")) + [ROOT / "README.md"]:
            with self.subTest(path.name):
                lines = path.read_text(encoding="utf-8").split("\n", 3)
                self.assertEqual(f"# {path.stem}", lines[0], "第一行和文件名一样")
                self.assertEqual("", lines[1])
                self.assertRegex(lines[2], r"^\*\*[^*\n]*[一-鿿][^*\n]*\*\*$", "下一行是加粗的中文副标题")

    def test_links_resolve_and_section_references_are_links(self) -> None:
        documents = list(SPEC.glob("*.md")) + [ROOT / "README.md"]
        targets = {p.resolve(): anchors(p) for p in documents}
        for path in documents:
            body = without_code(path.read_text(encoding="utf-8"))
            for target in re.findall(r"\]\(([^)\s]*)\)", body):
                if target.startswith("http"):
                    continue
                file_part, _, anchor = target.partition("#")
                resolved = (path.parent / file_part).resolve() if file_part else path.resolve()
                with self.subTest(document=path.name, target=target):
                    self.assertTrue(resolved.exists(), "引用的文件存在")
                    if anchor and resolved in targets:
                        self.assertTrue(anchor in targets[resolved], f"引用的锚点存在：{target}")
            if path.name != HISTORY and path.parent == SPEC:
                bare = re.findall(r"〈[^〉\n]{1,40}〉", re.sub(r"\[[^\]\n]*\]\([^)\s]*\)", "", body))
                with self.subTest(document=path.name):
                    self.assertEqual([], bare, "规范之间的指向都写成引用")

    def test_reference_titles_match_their_headings(self) -> None:
        for path in formal_documents() + [ROOT / "README.md"]:
            for name, number, title, _ in re.findall(r"\[([A-Za-z_]*)〈§([\d.]+) ([^〉]+)〉\]\(([^)]*)\)", path.read_text(encoding="utf-8")):
                source = SPEC / f"{name}.md" if name else path
                with self.subTest(document=path.name, reference=f"{name}§{number}"):
                    self.assertIn(f"§{number} {title}\n", source.read_text(encoding="utf-8"))

    def test_version_number_is_consistent(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        version = re.match(r"# README\n\n\*\*ACATE V(\d+\.\d+\.\d+) 工具包\*\*\n", readme).group(1)
        self.assertIn(f"ACATE-Review-Images/{version} ", (ROOT / "Script" / "ACATE_Script_Review_Images.py").read_text(encoding="utf-8"))
        self.assertIn(f"## {version}：", (SPEC / HISTORY).read_text(encoding="utf-8"))

    def test_example_images_and_references_match(self) -> None:
        images = {p.name for p in (ROOT / "Template").glob("*.png")}
        cited = set()
        for path in formal_documents():
            cited |= set(re.findall(r"(ACATE_Template_[A-Za-z0-9_]+\.png)", path.read_text(encoding="utf-8")))
        self.assertEqual(set(), cited - images, "规范引用的示例图都在 Template/")
        self.assertEqual(set(), images - cited, "Template/ 里的示例图都有规范引用")

    def test_comparison_image_is_in_the_package(self) -> None:
        self.assertTrue((ROOT / "Compare" / "ACATE_Compare.png").exists())

    def test_every_file_follows_the_prefix(self) -> None:
        for folder, prefix in (("Spec", "ACATE_Spec_"), ("Script", "ACATE_Script_"), ("Template", "ACATE_Template_"), ("Test", "ACATE_Test_")):
            for path in (ROOT / folder).iterdir():
                if path.is_file():
                    with self.subTest(path.name):
                        self.assertTrue(path.name.startswith(prefix))


if __name__ == "__main__":
    unittest.main()

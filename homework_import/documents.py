"""Read OOXML without Word, macros, conversion, or changes to student text."""
from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
NS = {"w": W, "r": R}
MAX_FILE = 80 * 1024 * 1024
MAX_UNPACKED = 240 * 1024 * 1024


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def xml(data: bytes):
    if len(data) > 30 * 1024 * 1024 or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("XML过大或包含不支持的实体声明")
    return ET.fromstring(data)


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def selected_children(node):
    """AlternateContent contains alternative representations, not extra content."""
    if node.tag == f"{{{MC}}}AlternateContent":
        choices = list(node)
        chosen = next((n for n in choices if local(n.tag) == "Choice"), None)
        if chosen is None:
            chosen = next((n for n in choices if local(n.tag) == "Fallback"), None)
        return list(chosen) if chosen is not None else []
    return list(node)


def read_document(path: Path, asset_dir: Path) -> dict:
    path = path.resolve()
    if path.stat().st_size > MAX_FILE:
        raise ValueError("文档超过80 MB限制")
    raw = path.read_bytes()
    if len(raw) > MAX_FILE:
        raise ValueError("文档超过80 MB限制")
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        raise ValueError("不是OOXML文档；旧版二进制.doc请另存为.docx后重新导入")
    blocks, warnings = [], []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        if len(infos) > 10000 or sum(i.file_size for i in infos) > MAX_UNPACKED:
            raise ValueError("文档解压大小或成员数量超限")
        if "word/document.xml" not in archive.namelist():
            raise ValueError("缺少word/document.xml")
        doc = xml(archive.read("word/document.xml"))
        rels = {}
        if "word/_rels/document.xml.rels" in archive.namelist():
            for rel in xml(archive.read("word/_rels/document.xml.rels")):
                rels[rel.get("Id")] = dict(rel.attrib)
        asset_dir.mkdir(parents=True, exist_ok=True)
        images = {}
        for rid, rel in rels.items():
            if not rel.get("Type", "").endswith("/image"):
                continue
            if rel.get("TargetMode") == "External":
                warnings.append("外链图片未下载：" + rid)
                continue
            target = posixpath.normpath(posixpath.join("word", rel.get("Target", "")))
            if target.startswith("/"):
                target = target.lstrip("/")
            if not target.startswith("word/") or target not in archive.namelist():
                warnings.append("图片关系无法解析：" + rid)
                continue
            content = archive.read(target)
            ext = Path(target).suffix.lower()
            safe = ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
            name = sha(content)[:24] + (ext if safe else ".bin")
            dest = asset_dir / name
            if not dest.exists():
                dest.write_bytes(content)
            images[rid] = {"file": name, "original": target, "preview": safe}

        # Resolve auto-numbered task headings without inserting numbers into code.
        numbering, instances, counters = {}, {}, {}
        if "word/numbering.xml" in archive.namelist():
            nums = xml(archive.read("word/numbering.xml"))
            for abstract in nums.findall("w:abstractNum", NS):
                levels = {}
                for level in abstract.findall("w:lvl", NS):
                    def val(name, default):
                        item = level.find("w:" + name, NS)
                        return item.get(f"{{{W}}}val", default) if item is not None else default
                    levels[level.get(f"{{{W}}}ilvl", "0")] = (val("numFmt", "decimal"), val("lvlText", "%1."), int(val("start", "1")))
                numbering[abstract.get(f"{{{W}}}abstractNumId")] = levels
            for num in nums.findall("w:num", NS):
                abstract = num.find("w:abstractNumId", NS)
                if abstract is not None:
                    instances[num.get(f"{{{W}}}numId")] = abstract.get(f"{{{W}}}val")

        def paragraph(p, location, context):
            text, refs, boxes, flags = [], [], [], []
            def content(n, loc):
                tag = local(n.tag)
                if n.tag == f"{{{W}}}txbxContent":
                    boxes.append((n, loc))
                    return
                if n.tag in {f"{{{W}}}del", f"{{{W}}}moveFrom"}:
                    flags.append("tracked_changes")
                    return
                if n.tag in {f"{{{W}}}ins", f"{{{W}}}moveTo"}:
                    flags.append("tracked_changes")
                if n.tag == f"{{{W}}}t":
                    text.append(n.text or "")
                elif n.tag == f"{{{W}}}tab":
                    text.append("\t")
                elif n.tag == f"{{{W}}}cr" or (n.tag == f"{{{W}}}br" and n.get(f"{{{W}}}type", "textWrapping") == "textWrapping"):
                    text.append("\n")
                elif tag in {"blip", "imagedata"}:
                    rid = n.get(f"{{{R}}}embed") or n.get(f"{{{R}}}id")
                    if rid in images and images[rid] not in refs:
                        refs.append(images[rid])
                elif tag in {"OLEObject", "object", "altChunk"}:
                    flags.append("embedded_object")
                elif n.tag == f"{{{W}}}sym":
                    flags.append("symbol_font")
                for j, child in enumerate(selected_children(n), 1):
                    content(child, loc + "/" + local(child.tag) + f"[{j}]")
            content(p, location)
            number = ""
            props = p.find("w:pPr/w:numPr", NS)
            if props is not None:
                ni, li = props.find("w:numId", NS), props.find("w:ilvl", NS)
                if ni is not None:
                    key = (ni.get(f"{{{W}}}val"), li.get(f"{{{W}}}val", "0") if li is not None else "0")
                    spec = numbering.get(instances.get(key[0]), {}).get(key[1])
                    if spec and spec[0] == "decimal":
                        count = counters.get(key, spec[2] - 1) + 1
                        counters[key] = count
                        number = spec[1].replace("%" + str(int(key[1]) + 1), str(count)) + " "
            blocks.append({"id": f"B{len(blocks)+1:05d}", "location": location,
                           "context": context, "text": "".join(text), "numbering": number,
                           "images": refs, "flags": sorted(set(flags))})
            for box, loc in boxes:
                walk(box, loc, "textbox")

        def walk(node, location, context="body"):
            counts = {}
            for child in selected_children(node):
                tag = local(child.tag)
                counts[tag] = counts.get(tag, 0) + 1
                loc = location + f"/{tag}[{counts[tag]}]"
                if child.tag == f"{{{W}}}p":
                    paragraph(child, loc, context)
                elif child.tag in {f"{{{W}}}del", f"{{{W}}}moveFrom"}:
                    warnings.append("文档存在修订删除内容；请核对最终版本")
                else:
                    if tag in {"ins", "moveTo"}:
                        warnings.append("文档存在修订插入内容；请核对最终版本")
                    if tag == "altChunk":
                        warnings.append("存在未解析的外部嵌入内容")
                    walk(child, loc, "table" if tag == "tbl" else context)
        body = doc.find("w:body", NS)
        if body is None:
            raise ValueError("文档没有正文")
        walk(body, "word/document.xml/body")
        if any(b["flags"] for b in blocks):
            warnings.append("存在修订、嵌入对象或特殊字体符号，请核对原文")
        # Do not silently claim coverage of source possibly placed outside the body.
        for name in archive.namelist():
            if re.fullmatch(r"word/(?:header\d+|footer\d+|footnotes|endnotes)\.xml", name):
                other = xml(archive.read(name))
                txt = "".join(n.text or "" for n in other.iter(f"{{{W}}}t"))
                if re.search(r"#\s*include|\bmain\s*\(", txt):
                    warnings.append("正文之外发现疑似源码，需人工补选：" + name)
    return {"path": str(path), "filename": path.name, "sha256": sha(raw),
            "format": "ooxml", "blocks": blocks, "warnings": sorted(set(warnings))}

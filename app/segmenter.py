"""本地题目分割模块。

流程：RapidOCR（本地 onnx 神经网络：DBNet 文本检测 + CRNN 识别，CPU 即可运行）
找出页面上形如 “12.” 的题号锚点，再按「分栏 + 题号锚点」把整页切成一道道题的
裁剪图；识别不到题号时退回基于空白投影的版面切块，保证总有可用结果。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# 顶层题号：如 “12.” “3．” “5、”（不匹配 “（1）” 这类小问编号）
ANCHOR_RE = re.compile(r"^\s*(\d{1,3})\s*[.．、]")

MAX_SIDE_OCR = 1600  # 送入 OCR 前的最大边长

_engine = None


def get_ocr():
    """懒加载 OCR 引擎（首次调用才载入模型，约几十 MB，CPU 数秒内可出结果）。"""
    global _engine
    if _engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:  # 兼容新版包名 rapidocr
            from rapidocr import RapidOCR
        logger.info("加载本地 OCR 模型（首次调用有几秒延迟）…")
        _engine = RapidOCR()
    return _engine


@dataclass
class TextBox:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


def _run_ocr(img: Image.Image) -> list[TextBox]:
    scale = min(1.0, MAX_SIDE_OCR / max(img.size))
    work = img
    if scale < 1.0:
        work = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        )
    arr = np.asarray(work.convert("RGB"))
    out = get_ocr()(arr)

    raw = None
    if isinstance(out, tuple):
        raw = out[0]
    elif hasattr(out, "boxes") and hasattr(out, "txts"):
        # rapidocr v2/v3 的 RapidOCROutput 结构
        if out.boxes is not None and out.txts is not None:
            raw = [[b, t, s] for b, t, s in zip(out.boxes, out.txts, out.scores)]
    else:
        raw = out

    boxes: list[TextBox] = []
    if not raw:
        return boxes
    for item in raw:
        try:
            pts, text = item[0], str(item[1]).strip()
        except Exception:
            continue
        if not text:
            continue
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        boxes.append(
            TextBox(min(xs) / scale, min(ys) / scale, max(xs) / scale, max(ys) / scale, text)
        )
    return boxes


def _detect_columns(boxes: list[TextBox], page_w: int):
    """按 x 方向的空白带判断单栏 / 双栏，返回 [(x0, x1), ...] 与每栏文本框列表。"""
    if not boxes:
        return [(0, page_w)], [boxes]
    bins = np.zeros(page_w + 2, dtype=int)
    for b in boxes:
        bins[int(max(0, b.x0)) : int(min(page_w, b.x1)) + 1] += 1
    lo, hi = int(page_w * 0.3), int(page_w * 0.7)
    best_len, best_start, run, run_start = 0, None, 0, lo
    for x in range(lo, hi):
        if bins[x] == 0:
            if run == 0:
                run_start = x
            run += 1
            if run > best_len:
                best_len, best_start = run, run_start
        else:
            run = 0
    if best_len >= page_w * 0.05:
        cx = best_start + best_len / 2
        left = [b for b in boxes if (b.x0 + b.x1) / 2 < cx]
        right = [b for b in boxes if (b.x0 + b.x1) / 2 >= cx]
        if left and right:
            return [(0, int(cx)), (int(cx), page_w)], [left, right]
    return [(0, page_w)], [boxes]


def _group_by_anchors(boxes: list[TextBox], col: tuple[int, int], page_h: int):
    """按题号锚点切分一栏，返回 [(y0, y1, 题号), ...]；无锚点返回 None。"""
    col_boxes = sorted(boxes, key=lambda b: (b.y0, b.x0))
    if not col_boxes:
        return None
    col_top = min(b.y0 for b in col_boxes)
    col_bottom = max(b.y1 for b in col_boxes)

    anchors: list[tuple[int, TextBox]] = []
    last_no = 0
    for b in col_boxes:
        m = ANCHOR_RE.match(b.text)
        if not m:
            continue
        no = int(m.group(1))
        if no > 200 or no <= last_no:  # 题号应严格递增，过滤正文里偶然出现的 “1.”
            continue
        last_no = no
        anchors.append((no, b))
    if not anchors:
        return None

    regions = []
    for i, (no, a) in enumerate(anchors):
        top = (a.y0 - 3) if i > 0 else col_top - 3  # 第一题吃掉页首的标题 / 大题号
        bottom = (anchors[i + 1][1].y0 - 3) if i + 1 < len(anchors) else col_bottom + 6
        top, bottom = max(0, top), min(page_h, max(bottom, top + 10))
        regions.append((int(top), int(bottom), no))
    return regions


def _projection_split(img: Image.Image) -> list[tuple[int, int]]:
    """兜底方案：按行投影找空白带切块，返回 [(y0, y1)]（原图坐标）。"""
    scale = 4
    w, h = img.size
    small = img.convert("L").resize((max(1, w // scale), max(1, h // scale)))
    ink = np.asarray(small) < 185
    row_ink = ink.sum(axis=1)
    peak = float(row_ink.max()) if row_ink.size else 0.0
    thr = max(1.0, peak * 0.02)
    content = row_ink > thr
    n = len(content)
    min_gap = max(2, n // 60)

    blocks: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if not content[i]:
            i += 1
            continue
        j, last_ink = i, i
        while j < n:
            if content[j]:
                last_ink = j
                j += 1
            elif j - last_ink >= min_gap:
                break
            else:
                j += 1
        blocks.append((i, last_ink + 1))
        i = last_ink + 1

    merged: list[tuple[int, int]] = []
    for y0, y1 in blocks:  # 合并过小的碎块
        if merged and (y1 - y0) < 10:
            merged[-1] = (merged[-1][0], y1)
        else:
            merged.append((y0, y1))
    return [(y0 * scale, min(h, y1 * scale)) for y0, y1 in merged]


def segment_page(img: Image.Image) -> dict:
    """分割一页作业图，返回 {"method", "crops": [{"bbox", "label", "is_full_page"?}]}。"""
    w, h = img.size
    crops: list[dict] = []
    method = "ocr"
    try:
        boxes = _run_ocr(img)
        columns, col_boxes = _detect_columns(boxes, w)
        regions = []
        for col, cb in zip(columns, col_boxes):
            r = _group_by_anchors(cb, col, h)
            if r:
                regions.extend((y0, y1, no, col) for y0, y1, no in r)
        regions.sort(key=lambda t: (t[3][0], t[0]))  # 先按栏、再按行，符合阅读顺序
        if not regions:
            raise ValueError("未找到题号锚点")
        for y0, y1, no, col in regions:
            crops.append({"bbox": [int(col[0]), y0, int(col[1]), y1], "label": f"第 {no} 题"})
    except Exception as e:  # noqa: BLE001 — 分割尽力而为，失败则兜底
        logger.info("锚点分割未生效（%s），退回投影切分", e)
        method = "projection"
        crops = [{"bbox": [0, int(y0), w, int(y1)], "label": None} for y0, y1 in _projection_split(img)]

    # 始终附加整页裁剪，作为兜底选项
    crops.append({"bbox": [0, 0, w, h], "label": "整页", "is_full_page": True})
    return {"method": method, "crops": crops}

"""AI 作业批改助手 — FastAPI 后端。"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel

from . import config, grader, store
from .segmenter import segment_page

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("homework-grader")

STATIC_DIR = Path(__file__).resolve().parent / "static"

MAX_SIDE_SAVE = 1600  # 页面图入库最大边长
MAX_SIDE_CROP = 1400  # 题目裁剪图最大边长
MAX_SIDE_AI = 1280    # 发给 AI 的图最大边长
JPEG_QUALITY = 88


def _norm(img: Image.Image, max_side: int) -> Image.Image:
    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > max_side:
        r = max_side / max(img.size)
        img = img.resize((round(img.width * r), round(img.height * r)), Image.LANCZOS)
    return img


def _save_jpeg(img: Image.Image, path: Path, max_side: int) -> None:
    _norm(img, max_side).save(path, "JPEG", quality=JPEG_QUALITY)


def _data_url(path: Path, max_side: int = MAX_SIDE_AI) -> str:
    img = Image.open(path)
    buf = io.BytesIO()
    _norm(img, max_side).save(buf, "JPEG", quality=JPEG_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _thumb_url(path: Path, max_side: int = 320) -> str:
    img = Image.open(path)
    buf = io.BytesIO()
    _norm(img, max_side).save(buf, "JPEG", quality=75)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.init_dirs()
    store.clean_tmp()
    yield


app = FastAPI(title="AI 作业批改", version="1.0.0", lifespan=lifespan)


# ========== 基础 ==========

@app.get("/api/health")
def health():
    return {
        "ok": True,
        "model": config.AI_MODEL,
        "base_url": config.AI_BASE_URL,
        "has_key": bool(config.API_KEY),
        "mock": config.AI_MOCK,
    }


# ========== 分割 ==========

@app.post("/api/segment")
async def api_segment(
    hw_files: list[UploadFile] = File(...),
    ans_files: list[UploadFile] = File(default=[]),
):
    sid = store.new_session()
    sdir = config.TMP_DIR / sid
    crops_out: list[dict] = []
    answer_ids: list[str] = []
    seg_method = None
    try:
        for page_idx, f in enumerate(hw_files):
            page_img = _norm(Image.open(io.BytesIO(await f.read())), MAX_SIDE_SAVE)
            page_path = sdir / f"page_{page_idx}.jpg"
            page_img.save(page_path, "JPEG", quality=JPEG_QUALITY)

            result = segment_page(page_img)
            seg_method = result["method"]
            for c in result["crops"]:
                cid = uuid.uuid4().hex[:10]
                crop_path = sdir / f"crop_{cid}.jpg"
                _save_jpeg(page_img.crop(tuple(c["bbox"])), crop_path, MAX_SIDE_CROP)
                crops_out.append(
                    {
                        "id": cid,
                        "label": c.get("label"),
                        "page": page_idx,
                        "is_full_page": bool(c.get("is_full_page")),
                        "thumb": _thumb_url(crop_path),
                    }
                )
        for f in ans_files or []:
            aid = uuid.uuid4().hex[:10]
            _save_jpeg(Image.open(io.BytesIO(await f.read())), sdir / f"ans_{aid}.jpg", MAX_SIDE_SAVE)
            answer_ids.append(aid)
    except Exception as e:
        logger.exception("分割失败")
        raise HTTPException(400, f"图片处理失败：{e}")

    if not crops_out:
        raise HTTPException(400, "未能从作业照片中得到任何可用区域")
    return {"session": sid, "crops": crops_out, "answer_ids": answer_ids, "method": seg_method}


# ========== 批改 ==========

class GradeReq(BaseModel):
    session: str
    crop_ids: list[str]
    answer_ids: list[str] = []
    subject: str = ""
    note: str = ""


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_client = None


def _get_client():
    global _client
    if _client is None:
        if not config.API_KEY:
            raise RuntimeError("未配置 API Key，请在 .env 中填写 DEEPSEEK_API_KEY")
        from openai import OpenAI

        _client = OpenAI(
            api_key=config.API_KEY, base_url=config.AI_BASE_URL, timeout=300, max_retries=1
        )
    return _client


def _open_stream(msgs: list[dict]):
    return _get_client().chat.completions.create(
        model=config.AI_MODEL,
        messages=msgs,
        stream=True,
        temperature=0.2,
        max_tokens=8000,
    )


def _complete(msgs: list[dict]) -> str:
    resp = _get_client().chat.completions.create(
        model=config.AI_MODEL, messages=msgs, temperature=0.0, max_tokens=8000
    )
    return resp.choices[0].message.content or ""


def _friendly_error(e: Exception) -> str:
    msg = str(e)
    if "401" in msg or "invalid" in msg.lower() and "key" in msg.lower():
        return "API Key 无效，请检查 .env 里的 DEEPSEEK_API_KEY"
    if "429" in msg:
        return "请求过于频繁或额度不足（429），稍后再试或检查账户余额"
    if "timed out" in msg.lower() or "timeout" in msg.lower():
        return "AI 请求超时，题目太多时请分两批勾选重试"
    return f"AI 调用失败：{msg[:300]}"


def _mock_pieces() -> list[str]:
    s = grader.MOCK_REPLY
    size = max(8, len(s) // 24)
    return [s[i : i + size] for i in range(0, len(s), size)]


def _count_verdicts(questions: list[dict], total: int) -> dict:
    s = {"total": total, "right": 0, "wrong": 0, "partial": 0, "unknown": 0}
    for q in questions:
        v = str(q.get("verdict") or "")
        if "部分" in v:
            s["partial"] += 1
        elif "错" in v or "误" in v:
            s["wrong"] += 1
        elif "对" in v or "正确" in v:
            s["right"] += 1
        else:
            s["unknown"] += 1
    return s


async def _grade_stream(crop_paths: list[Path], ans_paths: list[Path], subject: str, note: str):
    started = time.time()
    crop_urls = [_data_url(p) for p in crop_paths]
    ans_urls = [_data_url(p) for p in ans_paths]
    n = len(crop_urls)

    # 图太多时自动分批（答案页每批都带上）；默认情况下全部题目一次请求完成
    per = max(4, config.MAX_IMAGES_PER_REQUEST - len(ans_urls))
    chunks = [crop_urls[i : i + per] for i in range(0, n, per)] or [crop_urls]
    multi = len(chunks) > 1

    yield _sse(
        "status",
        {
            "stage": "ai",
            "text": f"AI 批改中：{n} 题一次请求完成"
            if not multi
            else f"AI 批改中：共 {n} 题，分 {len(chunks)} 批请求",
        },
    )

    all_questions: list[dict] = []
    advices: list[str] = []

    for ci, chunk in enumerate(chunks):
        offset = sum(len(c) for c in chunks[:ci])
        msgs = grader.build_messages(chunk, ans_urls, subject, note, offset=offset)
        if multi:
            yield _sse("status", {"stage": "ai", "text": f"AI 批改中：第 {ci + 1}/{len(chunks)} 批…"})

        if config.AI_MOCK:
            text = "".join(_mock_pieces())
            for piece in _mock_pieces():
                yield _sse("delta", {"text": piece})
                await asyncio.sleep(0.05)
            data = grader.extract_json(text)
        else:
            text = ""
            try:
                it = await asyncio.to_thread(_open_stream, msgs)
                while True:
                    ev = await asyncio.to_thread(next, it, None)
                    if ev is None:
                        break
                    delta = ev.choices[0].delta.content if ev.choices else None
                    if delta:
                        text += delta
                        yield _sse("delta", {"text": delta})
            except Exception as e:
                logger.exception("AI 调用失败")
                yield _sse("error", {"message": _friendly_error(e)})
                return

            try:
                data = grader.extract_json(text)
            except Exception:
                yield _sse("status", {"stage": "ai", "text": "结果整理中（格式修正）…"})
                try:
                    fixed = await asyncio.to_thread(
                        _complete,
                        [
                            {"role": "user", "content": msgs[-1]["content"]},
                            {"role": "assistant", "content": text},
                            {
                                "role": "user",
                                "content": "请把上面的批改结果整理为纯 JSON 输出，不要输出任何其他文字。",
                            },
                        ],
                    )
                    data = grader.extract_json(fixed)
                except Exception as e:
                    yield _sse("error", {"message": f"AI 返回内容无法解析为 JSON：{e}"})
                    return

        qs = data.get("questions") or []
        for q in qs:
            try:
                q["index"] = int(q.get("index") or 0) + offset
            except Exception:
                q["index"] = 0
        all_questions.extend(qs)
        sm = data.get("summary") or {}
        if sm.get("advice"):
            advices.append(str(sm["advice"]))

    all_questions.sort(key=lambda q: q.get("index") or 0)
    summary = _count_verdicts(all_questions, n)
    summary["advice"] = " ".join(a for a in advices if a)

    record = {
        "subject": subject,
        "note": note,
        "model": config.AI_MODEL,
        "questions": all_questions,
        "summary": summary,
        "thumbs": [_thumb_url(p) for p in crop_paths],
        "elapsed": round(time.time() - started, 1),
    }
    record["history_id"] = store.save_history(record)
    logger.info("批改完成：%d 题，用时 %.1fs", n, time.time() - started)
    yield _sse("done", {"result": record})


@app.post("/api/grade")
async def api_grade(req: GradeReq):
    if not req.session.isalnum() or not (config.TMP_DIR / req.session).is_dir():
        raise HTTPException(400, "会话不存在或已过期，请重新上传")
    sdir = config.TMP_DIR / req.session

    crop_paths: list[Path] = []
    for cid in req.crop_ids:
        if not cid.isalnum():
            raise HTTPException(400, "非法题目 ID")
        p = sdir / f"crop_{cid}.jpg"
        if not p.exists():
            raise HTTPException(400, "题目图片不存在，请重新上传")
        crop_paths.append(p)
    if not crop_paths:
        raise HTTPException(400, "请至少勾选一道题")

    ans_paths: list[Path] = []
    for aid in req.answer_ids:
        if aid.isalnum():
            p = sdir / f"ans_{aid}.jpg"
            if p.exists():
                ans_paths.append(p)

    return StreamingResponse(
        _grade_stream(crop_paths, ans_paths, req.subject.strip(), req.note.strip()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ========== 历史 ==========

@app.get("/api/history")
def api_history():
    return store.list_history()


@app.get("/api/history/{hid}")
def api_history_detail(hid: str):
    rec = store.load_history(hid)
    if rec is None:
        raise HTTPException(404, "记录不存在")
    return rec


# ========== 静态前端 ==========

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

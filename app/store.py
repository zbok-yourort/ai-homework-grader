"""会话临时文件与批改历史的磁盘存储。"""
from __future__ import annotations

import json
import shutil
import time
import uuid

from . import config


def init_dirs():
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    config.HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def clean_tmp():
    """启动时清掉上次遗留的临时会话文件。"""
    if config.TMP_DIR.exists():
        for p in config.TMP_DIR.iterdir():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)


def new_session() -> str:
    sid = uuid.uuid4().hex
    (config.TMP_DIR / sid).mkdir(parents=True, exist_ok=True)
    return sid


def save_history(record: dict) -> str:
    hid = uuid.uuid4().hex[:12]
    record["id"] = hid
    record["created_at"] = int(time.time())
    (config.HISTORY_DIR / f"{hid}.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )
    return hid


def list_history(limit: int = 50) -> list[dict]:
    files = sorted(
        config.HISTORY_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    items = []
    for f in files:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            s = rec.get("summary") or {}
            items.append(
                {
                    "id": rec["id"],
                    "created_at": rec["created_at"],
                    "subject": rec.get("subject") or "",
                    "total": s.get("total"),
                    "right": s.get("right"),
                    "wrong": s.get("wrong"),
                    "partial": s.get("partial"),
                }
            )
        except Exception:
            continue
    return items


def load_history(hid: str) -> dict | None:
    if not hid.isalnum():  # 防路径穿越
        return None
    f = config.HISTORY_DIR / f"{hid}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None

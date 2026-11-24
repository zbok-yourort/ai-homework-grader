"""AI 批改模块：OpenAI 兼容接口调用视觉模型（默认 DeepSeek）。

题目裁剪图按顺序一次性放进同一条 user 消息（多张图一个请求），
而不是一道题一个请求；图太多时由后端自动分批顺序调用。
"""
from __future__ import annotations

import json
import re

from . import config

SYSTEM_PROMPT = """你是一位经验丰富的中国中学各科老师，负责批改学生作业。用户会提供：
1) 按顺序编号的学生作业题目裁剪图（第 1 张图对应 index 1，依次类推）；
2) 可选的标准答案页照片（有文字标注说明）。

你的任务：对照参考答案逐题批改，要求：
- 每题判定为：对 / 错 / 部分对 / 无法判断
- 数学、物理等需要步骤的题目要检查解题过程，指出出错的具体步骤和对应知识点
- 若未提供参考答案，可依据你的学科知识判断，但必须在 explanation 中注明「未提供参考答案，由 AI 依据学科知识判断」
- 学生漏做 / 空白的题判为「错」，mistake 里写「漏做」
- 图片模糊、裁剪不完整导致无法可靠判断时，verdict 用「无法判断」，并在 explanation 说明原因
- 严格、诚实：不放过真实错误，也不编造错误

只输出一个 JSON 对象，禁止输出 Markdown 代码块或任何其他文字，格式如下：
{"questions": [{"index": 题目图片序号(从1开始), "no": "题号，如 12", "verdict": "对|错|部分对|无法判断", "score": "得分如 3/5，无法判断时为 null", "student_answer": "学生作答要点，简短", "correct_answer": "正确答案要点", "mistake": "错因与出错步骤定位，做对时为 null", "explanation": "1-3 句讲解或订正建议", "tags": ["涉及知识点"]}], "summary": {"total": 题数, "right": 对数, "wrong": 错数, "partial": 部分对数, "unknown": 无法判断数, "advice": "整体评价与复习建议，1-2 句"}}"""


def build_messages(
    crops: list[str], answers: list[str], subject: str, note: str, offset: int = 0
) -> list[dict]:
    content: list[dict] = []
    intro = f"科目/年级：{subject or '未提供'}\n补充说明：{note or '无'}\n\n"
    if offset:
        intro += f"注意：这是同一份作业的后续部分，题图序号从 {offset + 1} 开始继续编号。\n\n"
    intro += f"下面按顺序给出 {len(crops)} 张题目裁剪图（第 1 张对应 index 1，依此类推）。"
    content.append({"type": "text", "text": intro})
    for url in crops:
        content.append({"type": "image_url", "image_url": {"url": url}})
    if answers:
        content.append({"type": "text", "text": "以下是标准答案页照片，供对照批改："})
        for url in answers:
            content.append({"type": "image_url", "image_url": {"url": url}})
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("回复中未找到 JSON")
    return json.loads(text[start : end + 1])


MOCK_REPLY = json.dumps(
    {
        "questions": [
            {
                "index": 1, "no": "1", "verdict": "对", "score": "5/5",
                "student_answer": "x = 3", "correct_answer": "x = 3",
                "mistake": None, "explanation": "移项与合并同类项均正确，答案无误。",
                "tags": ["一元一次方程"],
            },
            {
                "index": 2, "no": "2", "verdict": "错", "score": "0/5",
                "student_answer": "x = ±2", "correct_answer": "x = 2（x = -2 是增根，舍去）",
                "mistake": "第二步：解出根后没有检验，x = -2 使根号内为负，是增根",
                "explanation": "解含根号或分母的方程时，最后必须代入原方程检验，舍去不满足定义域的根。",
                "tags": ["增根检验", "定义域"],
            },
            {
                "index": 3, "no": "3", "verdict": "部分对", "score": "2/5",
                "student_answer": "画出重力与支持力，漏了摩擦力",
                "correct_answer": "重力、支持力、沿斜面向上的摩擦力",
                "mistake": "漏画沿斜面向上的摩擦力",
                "explanation": "物体静止在粗糙斜面上时，摩擦力方向沿斜面向上，与重力沿斜面的分力平衡。",
                "tags": ["受力分析", "摩擦力"],
            },
        ],
        "summary": {
            "total": 3, "right": 1, "wrong": 1, "partial": 1, "unknown": 0,
            "advice": "基础扎实，注意含根式方程的增根检验和受力分析的完整性，建议重做第 2、3 题。",
        },
    },
    ensure_ascii=False,
)

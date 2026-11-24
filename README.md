# AI 作业批改助手 (AI Homework Grader)

拍照 → 本地 AI 分题 → 一次请求交给视觉大模型 → 逐题批改：对错判定、错因定位、讲解订正。全科通用，专为中学生「对答案」场景打造。

## ✨ 特性

- **本地题目分割**：内置轻量 OCR 神经网络（DBNet 检测 + CRNN 识别，onnxruntime，CPU 数秒），按题号锚点 + 分栏把整页试卷切成一道道题；识别不稳时自动退回版面切块，UI 里还能手动勾选/取消
- **一次请求批改**：所有题目裁剪图按顺序打包进**同一次** AI 请求（不一道题一个请求），省时省钱
- **全科通用**：数学看解题步骤并定位出错步骤，英语/语文等看结果判定，无参考答案时 AI 依据学科知识判断并注明
- **流式体验**：AI 输出实时滚动显示，结果自动解析成结构化卡片（对 / 错 / 部分对 / 无法判断 + 得分 + 错因 + 讲解 + 知识点）
- **历史记录**：每次批改自动存本机，可回看
- **隐私友好**：分割完全本地；图片只发往你自己配置的 AI 服务商；所有数据存在本机 `data/` 目录
- **可换模型**：OpenAI 兼容接口，默认 DeepSeek，改两个环境变量即可换智谱 / 通义等

## 🔧 工作原理

```mermaid
flowchart LR
    A[📸 拍作业/答案页] --> B[✂️ 本地 OCR 神经网络<br/>题号锚点 + 分栏切割]
    B --> C[👀 人工确认裁剪<br/>可勾选/取消]
    C --> D[🤔 全部题目图打包<br/>一次视觉模型请求]
    D --> E[📊 逐题批改卡片<br/>对错/错因/讲解]
    E --> F[(💾 本机历史)]
```

分割细节：RapidOCR 找出形如 `12.` 的顶层题号行作为锚点 → 按空白带自动识别单/双栏 → 相邻锚点之间切成一题；第一题自动包含页首题干，找不到锚点时按行投影空白切块兜底。

## 🚀 快速开始

需要 Python 3.10+（推荐 3.12/3.13），无需 GPU。

```bash
# 1. 克隆并安装依赖
git clone https://github.com/<你的用户名>/ai-homework-grader.git
cd ai-homework-grader
pip install -r requirements.txt

# 2. 配置 API Key（DeepSeek 开放平台申请：https://platform.deepseek.com）
cp .env.example .env
#   编辑 .env，填入 DEEPSEEK_API_KEY=sk-...

# 3. 启动
python run.py
```

打开 `http://localhost:8000` 即可。**手机使用**：手机和电脑连同一 Wi-Fi，用手机浏览器访问 `http://<电脑局域网IP>:8000`（`.env` 中 `HOST=0.0.0.0` 已默认开启），直接拍照上传。

> 没有 API Key 想先看效果？在 `.env` 里设 `AI_MOCK=1`，会返回模拟批改结果。

## ⚙️ 配置说明

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 空 | DeepSeek API Key（必填，mock 模式除外） |
| `AI_MODEL` | `deepseek-flash` | 模型名，该模型已支持图片输入；旧名 `deepseek-v4-flash-vision-exp` 也可用 |
| `AI_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容接口地址，换厂商改这里 |
| `AI_MOCK` | `0` | 演示模式：不调真实 API，返回模拟结果 |
| `MAX_IMAGES_PER_REQUEST` | `18` | 单次 AI 请求最多带几张图，超出自动分批顺序请求 |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | 服务监听地址与端口 |

**换成其他厂商**（需为 OpenAI 兼容且支持图片输入的模型）：

```env
# 智谱 GLM
AI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
AI_MODEL=glm-4v-flash

# 阿里通义
AI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AI_MODEL=qwen-vl-plus
```

## 📁 目录结构

```
app/
├── main.py        # FastAPI 后端：分割 / 批改(SSE 流式) / 历史 / 静态托管
├── segmenter.py   # 本地题目分割：OCR 锚点 + 分栏分组 + 投影兜底
├── grader.py      # AI 批改：多图一次请求 + JSON 解析与自动修正
├── store.py       # 会话临时文件与历史记录
├── config.py      # .env 配置
└── static/        # 前端单页（原生 HTML/CSS/JS，无需构建）
```

## ❓ FAQ

**分割不准怎么办？**
分割是尽力而为的预处理：裁剪块可以手动取消勾选；分得太碎时可改选「整页」卡片，把整页直接交给 AI 批改。

**一道题一个请求 vs 一次请求？**
本工具把所有题目图放进同一条消息一次发出，仅当图片数超过 `MAX_IMAGES_PER_REQUEST` 时才自动分批——既省请求次数，也让 AI 能结合上下文（如大题干跨图）批改。

**数据安全吗？**
作业图片仅保存在本机 `data/` 目录（已加入 `.gitignore`）；批改请求只发往你在 `.env` 里配置的 AI 服务商。请勿把作业照片上传到不受信任的第三方服务。

**批改结果可信吗？**
AI 批改可能出错，尤其是手写内容模糊时。它适合日常自查练习，重要考试请以老师批改为准。

## 🤝 参与

欢迎提 Issue / PR：分割算法优化（如支持手写题号、作文格）、更多科目的批改提示词、结果导出等都是好方向。

## 📄 许可

[MIT](LICENSE) © 2026 AI-Homework-Grader Contributors

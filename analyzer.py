from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
ENV_PATH = PROJECT_DIR / ".env"
DATA_DIR = PROJECT_DIR / "work" / "vn_jp_tool_data"
ENHANCED_SCREENSHOT = DATA_DIR / "last_capture_enhanced.png"
BIGMODEL_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


SYSTEM_PROMPT = """
你是一个帮助中文母语者阅读日语视觉小说的学习助手。
请严格返回 JSON，不要返回 Markdown。

目标：
1. 如果用户提供图片，先 OCR 识别其中的日语对白或界面文字。忽略窗口边框、水印、鼠标、UI 装饰、背景图案，只提取真正的日语文本。
2. 给所有汉字标注平假名，格式使用 漢字(かな)，不要用 HTML ruby。只要字符是汉字，就必须在 furigana_text 中给出读音，不能漏标。
   汉字和假名混合的词也必须只给汉字部分标注，例如 思った 要写成 思(おも)った，押さえつけながら 要写成 押(お)さえつけながら。
3. 提取值得学习的单词，尤其是汉字词、动词、形容词、惯用表达。
4. 解释重点语法，说明语法功能和自然中文含义。每个语法点必须给一个新造日语例句，不要复制原文句子。
5. 提取固定搭配、惯用表达、常见动词搭配、视觉小说常见表达。每个搭配必须说明中文意思、使用场景，并给一个新造日语例句，不要复制原文句子。
6. 给出整段中文翻译。

JSON 结构：
{
  "original_text": "识别或输入的日文原文",
  "furigana_text": "所有汉字都用 漢字(かな) 标注后的日文；汉字假名混合词只标汉字部分，如 思(おも)った",
  "translation_zh": "自然中文翻译",
  "vocabulary": [
    {
      "expression": "単語",
      "reading": "たんご",
      "meaning_zh": "中文意思",
      "part_of_speech": "词性",
      "note": "简短说明"
    }
  ],
  "grammar": [
    {
      "pattern": "语法形式",
      "explanation_zh": "中文解释",
      "new_example_ja": "新造日语例句，不要使用原文句子",
      "new_example_zh": "新造例句的中文翻译"
    }
  ],
  "collocations": [
    {
      "expression": "固定搭配或惯用表达",
      "reading": "读音",
      "meaning_zh": "中文意思",
      "usage_zh": "使用场景或语气说明",
      "new_example_ja": "新造日语例句，不要使用原文句子",
      "new_example_zh": "新造例句的中文翻译"
    }
  ]
}
如果图片没有可识别日文，original_text 使用空字符串，并在 translation_zh 简短说明。
""".strip()


@dataclass
class AnalysisResult:
    original_text: str = ""
    furigana_text: str = ""
    translation_zh: str = ""
    vocabulary: list[dict[str, str]] = field(default_factory=list)
    grammar: list[dict[str, str]] = field(default_factory=list)
    collocations: list[dict[str, str]] = field(default_factory=list)
    raw_text: str = ""


class AnalyzerError(RuntimeError):
    pass


def load_env_file(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class BigModelAnalyzer:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        load_env_file()
        self.api_key = api_key or os.getenv("BIGMODEL_API_KEY") or os.getenv("ZHIPUAI_API_KEY", "")
        self.model = model or os.getenv("BIGMODEL_MODEL", "GLM-4.6V-FlashX")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def analyze_text(self, text: str) -> AnalysisResult:
        if not text.strip():
            raise AnalyzerError("请先输入或识别一段日文。")
        return self._request(text=text, image_path=None)

    def analyze_image(self, image_path: Path) -> AnalysisResult:
        if not image_path.exists():
            raise AnalyzerError("找不到截图文件。")
        prepared = self._prepare_image(image_path)
        return self._request(text="", image_path=prepared)

    def _prepare_image(self, image_path: Path) -> Path:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        image = Image.open(image_path).convert("RGB")
        width, height = image.size
        scale = 2 if max(width, height) < 1800 else 1
        if scale > 1:
            image = image.resize((width * scale, height * scale), Image.Resampling.LANCZOS)
        image = ImageOps.grayscale(image)
        image = ImageOps.autocontrast(image, cutoff=1)
        image = ImageEnhance.Contrast(image).enhance(1.35)
        image = image.filter(ImageFilter.SHARPEN)
        image.save(ENHANCED_SCREENSHOT)
        return ENHANCED_SCREENSHOT

    def _request(self, text: str, image_path: Path | None) -> AnalysisResult:
        if not self.api_key:
            raise AnalyzerError("还没有设置 BIGMODEL_API_KEY。")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._build_user_content(text=text, image_path=image_path)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "stream": False,
        }

        request = urllib.request.Request(
            BIGMODEL_API_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            if error.code == 429:
                raise AnalyzerError("BigModel 当前访问量过大或额度受限，请稍后再试。") from error
            if error.code == 503:
                raise AnalyzerError("BigModel 当前模型访问量过高，请稍后再试。") from error
            raise AnalyzerError(f"BigModel 请求失败：{error.code} {detail}") from error
        except urllib.error.URLError as error:
            raise AnalyzerError(f"无法连接 BigModel：{error.reason}") from error

        output_text = self._extract_output_text(body)
        return self._parse_result(output_text)

    def _build_user_content(self, text: str, image_path: Path | None) -> list[dict[str, Any]]:
        if image_path is None:
            return [
                {
                    "type": "text",
                    "text": (
                        "下面这一行就是需要分析的有效日文文本，不需要 OCR。"
                        "请直接把它抄入 original_text，然后标注假名、解释单词、语法、固定搭配，并翻译成中文。\n\n"
                        f"{text.strip()}"
                    ),
                }
            ]

        encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        return [
            {
                "type": "text",
                "text": (
                    "请识别图片中的日语文本，并按指定 JSON 结构给出假名、语法、固定搭配、翻译和单词。"
                    "图片已经过放大和增强；请尽量按日语上下文纠正 OCR 易混字符。"
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            },
        ]

    def _extract_output_text(self, body: dict[str, Any]) -> str:
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise AnalyzerError(f"BigModel 返回里没有可读取的文本：{body}") from error

        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    chunks.append(item["text"])
            if chunks:
                return "\n".join(chunks)
        raise AnalyzerError(f"BigModel 返回格式暂时无法读取：{body}")

    def _parse_result(self, output_text: str) -> AnalysisResult:
        cleaned = output_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as error:
            raise AnalyzerError(f"AI 返回的 JSON 解析失败：{error}\n\n原始返回：{output_text}") from error

        return AnalysisResult(
            original_text=str(data.get("original_text", "")),
            furigana_text=str(data.get("furigana_text", "")),
            translation_zh=str(data.get("translation_zh", "")),
            vocabulary=self._normalize_list(data.get("vocabulary")),
            grammar=self._normalize_list(data.get("grammar")),
            collocations=self._normalize_list(data.get("collocations")),
            raw_text=output_text,
        )

    def _normalize_list(self, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, str]] = []
        for item in value:
            if isinstance(item, dict):
                normalized.append({str(key): str(val) for key, val in item.items()})
        return normalized


OpenAIAnalyzer = BigModelAnalyzer
GeminiAnalyzer = BigModelAnalyzer

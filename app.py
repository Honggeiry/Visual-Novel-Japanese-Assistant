from __future__ import annotations

import base64
import json
import re
import subprocess
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageGrab, ImageTk

try:
    from .analyzer import AnalysisResult, AnalyzerError, BigModelAnalyzer
    from .storage import VocabEntry, VocabStore
except ImportError:
    from analyzer import AnalysisResult, AnalyzerError, BigModelAnalyzer
    from storage import VocabEntry, VocabStore


APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
DATA_DIR = PROJECT_DIR / "work" / "vn_jp_tool_data"
OUTPUT_DIR = PROJECT_DIR / "outputs" / "vn_jp_tool"
LAST_SCREENSHOT = DATA_DIR / "last_capture.png"
LAST_REGION = DATA_DIR / "last_region.json"
DB_PATH = DATA_DIR / "vocabulary.sqlite3"
TTS_TEXT_PATH = DATA_DIR / "tts_text.txt"

KANJI_RE = re.compile(r"[\u3400-\u9fff\u3005\u3006\u30f6]")
KANJI_GROUP_RE = re.compile(r"([\u3400-\u9fff\u3005\u3006\u30f6]+)\(([^)]+)\)")
KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ffー]+")


class RubyTextCanvas(ttk.Frame):
    def __init__(self, parent: tk.Widget, base_size: int = 14) -> None:
        super().__init__(parent)
        self.text = ""
        self.base_size = base_size
        self.ruby_size = max(7, base_size - 6)
        self.base_font = tkfont.Font(family="Meiryo", size=self.base_size)
        self.ruby_font = tkfont.Font(family="Meiryo", size=self.ruby_size)
        self.canvas = tk.Canvas(self, bg="white", highlightthickness=1, highlightbackground="#d8d8d8")
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas.bind("<Configure>", lambda _event: self._render())
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)

    def set_text(self, value: str) -> None:
        self.text = value.strip()
        self._render()

    def set_font_size(self, size: int) -> None:
        self.base_size = max(9, min(30, size))
        self.ruby_size = max(7, self.base_size - 6)
        self.base_font.configure(size=self.base_size)
        self.ruby_font.configure(size=self.ruby_size)
        self._render()

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _parse_tokens(self) -> list[tuple[str, str | None]]:
        tokens: list[tuple[str, str | None]] = []
        pos = 0
        for match in KANJI_GROUP_RE.finditer(self.text):
            if match.start() > pos:
                tokens.extend((char, None) for char in self.text[pos : match.start()])
            tokens.append((match.group(1), match.group(2)))
            pos = match.end()
        if pos < len(self.text):
            tokens.extend((char, None) for char in self.text[pos:])
        return tokens

    def _render(self) -> None:
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 320)
        x = 10
        line_top = 8
        line_height = self.base_size + self.ruby_size + 24
        base_y = self.ruby_size + 10
        max_x = width - 20

        for base, ruby in self._parse_tokens():
            if base == "\n":
                x = 10
                line_top += line_height
                continue
            token_width = max(self.base_font.measure(base), self.ruby_font.measure(ruby or "")) + (8 if ruby else 0)
            if x + token_width > max_x and x > 10:
                x = 10
                line_top += line_height
            if ruby:
                center = x + token_width / 2
                self.canvas.create_text(center, line_top, text=ruby, font=self.ruby_font, anchor="n", fill="#555")
                self.canvas.create_text(center, line_top + base_y, text=base, font=self.base_font, anchor="n", fill="#111")
            else:
                self.canvas.create_text(x, line_top + base_y, text=base, font=self.base_font, anchor="nw", fill="#111")
            x += token_width

        self.canvas.configure(scrollregion=(0, 0, width, line_top + line_height + 8))


class RegionSelector(tk.Toplevel):
    def __init__(self, parent: tk.Tk, callback) -> None:
        super().__init__(parent)
        self.callback = callback
        self.start_x = 0
        self.start_y = 0
        self.rect_id = None

        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.28)
        self.configure(bg="black")
        self.canvas = tk.Canvas(self, cursor="cross", bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Escape>", lambda _event: self.destroy())

    def on_press(self, event) -> None:
        self.start_x = event.x_root
        self.start_y = event.y_root
        self.rect_id = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#fff176", width=3)

    def on_drag(self, event) -> None:
        if self.rect_id is not None:
            self.canvas.coords(self.rect_id, self.start_x, self.start_y, event.x_root, event.y_root)

    def on_release(self, event) -> None:
        bbox = (
            min(self.start_x, event.x_root),
            min(self.start_y, event.y_root),
            max(self.start_x, event.x_root),
            max(self.start_y, event.y_root),
        )
        self.destroy()
        if bbox[2] - bbox[0] < 10 or bbox[3] - bbox[1] < 10:
            messagebox.showwarning("区域太小", "请框选包含文字的区域。")
            return
        self.callback(bbox)


class VNJapaneseTool(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("视觉小说日语助手")
        self.geometry("1240x820")
        self.minsize(1040, 700)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.store = VocabStore(DB_PATH)
        self.analyzer = BigModelAnalyzer()
        self.current_result = AnalysisResult()
        self.preview_image = None
        self.font_sizes = {
            "original": tk.IntVar(value=12),
            "furigana": tk.IntVar(value=14),
            "translation": tk.IntVar(value=10),
            "grammar": tk.IntVar(value=10),
            "vocab": tk.IntVar(value=12),
        }

        self._build_style()
        self._build_ui()
        self.refresh_vocab()

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 14, "bold"))
        style.configure("Section.TLabel", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Status.TLabel", foreground="#555")
        style.configure("Vocab.Treeview", font=("Meiryo", 12), rowheight=32)
        style.configure("Vocab.Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="视觉小说日语助手", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.status_var = tk.StringVar(value=self._initial_status())
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=1, sticky="e")

        self.main_panes = tk.PanedWindow(root, orient=tk.HORIZONTAL, sashwidth=8, sashrelief=tk.RAISED, bg="#dddddd")
        self.main_panes.grid(row=1, column=0, sticky="nsew")

        left = ttk.Frame(self.main_panes)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(3, weight=1)
        self.main_panes.add(left, minsize=520, width=760)

        controls = ttk.Frame(left)
        controls.grid(row=0, column=0, sticky="ew")
        ttk.Button(controls, text="选择文字区域", command=self.select_region).pack(side="left", padx=(0, 6))
        ttk.Button(controls, text="识别截图并分析", command=self.analyze_screenshot).pack(side="left", padx=6)
        ttk.Button(controls, text="复扫上次区域", command=self.rescan_last_region).pack(side="left", padx=6)
        ttk.Button(controls, text="分析粘贴文本", command=self.analyze_manual_text).pack(side="left", padx=6)
        ttk.Button(controls, text="打开截图", command=self.open_image).pack(side="left", padx=6)

        self.preview_label = ttk.Label(left, text="截图预览会显示在这里")
        self.preview_label.grid(row=1, column=0, sticky="ew", pady=(10, 8))

        self.left_panes = tk.PanedWindow(left, orient=tk.VERTICAL, sashwidth=7, sashrelief=tk.RAISED, bg="#dddddd")
        self.left_panes.grid(row=3, column=0, sticky="nsew")

        original_frame = self._make_panel(self.left_panes, "原文（也可以手动粘贴）", "original")
        ttk.Button(original_frame.header, text="日语朗读", command=self.speak_original).pack(side="right", padx=(8, 0))
        self.original_text = tk.Text(original_frame.body, height=3, wrap="word", font=("Meiryo", 12))
        self.original_text.pack(fill="both", expand=True)
        self.left_panes.add(original_frame, minsize=80, height=110)

        furigana_frame = self._make_panel(self.left_panes, "假名标注", "furigana")
        self.furigana_view = RubyTextCanvas(furigana_frame.body, base_size=14)
        self.furigana_view.pack(fill="both", expand=True)
        self.left_panes.add(furigana_frame, minsize=110, height=180)

        translation_frame = self._make_panel(self.left_panes, "中文翻译", "translation")
        self.translation_text = tk.Text(translation_frame.body, height=3, wrap="word", font=("Microsoft YaHei UI", 10))
        self.translation_text.pack(fill="both", expand=True)
        self.left_panes.add(translation_frame, minsize=75, height=100)

        grammar_frame = self._make_panel(self.left_panes, "语法", "grammar")
        self.grammar_text = tk.Text(grammar_frame.body, height=8, wrap="word", font=("Microsoft YaHei UI", 10))
        self.grammar_text.pack(fill="both", expand=True)
        self.left_panes.add(grammar_frame, minsize=140, height=270)

        right = ttk.Frame(self.main_panes)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self.main_panes.add(right, minsize=360, width=440)

        self.right_panes = tk.PanedWindow(right, orient=tk.VERTICAL, sashwidth=7, sashrelief=tk.RAISED, bg="#dddddd")
        self.right_panes.grid(row=0, column=0, sticky="nsew")

        detected_frame = self._make_panel(self.right_panes, "识别出的重点单词", "vocab")
        self.vocab_tree = self._make_vocab_tree(detected_frame.body, selectmode="extended")
        self.vocab_tree.bind("<Double-1>", self._on_double_click_vocab)
        self.vocab_tree.pack(fill="both", expand=True)
        vocab_buttons = ttk.Frame(detected_frame.body)
        vocab_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(vocab_buttons, text="收藏选中单词", command=self.save_selected_word).pack(side="left")
        ttk.Button(vocab_buttons, text="全部收藏", command=self.save_all_words).pack(side="left", padx=8)
        self.right_panes.add(detected_frame, minsize=150, height=310)

        saved_frame = self._make_panel(self.right_panes, "单词本", "vocab")
        self.saved_tree = self._make_vocab_tree(saved_frame.body, selectmode="browse", include_time=True)
        self.saved_tree.bind("<Double-1>", self._on_double_click_saved)
        self.saved_tree.pack(fill="both", expand=True)
        saved_buttons = ttk.Frame(saved_frame.body)
        saved_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(saved_buttons, text="删除选中", command=self.delete_saved_word).pack(side="left")
        ttk.Button(saved_buttons, text="刷新", command=self.refresh_vocab).pack(side="left", padx=8)
        ttk.Button(saved_buttons, text="导出 CSV", command=self.export_csv).pack(side="left")
        self.right_panes.add(saved_frame, minsize=150, height=310)

    def _make_panel(self, parent: tk.PanedWindow, title: str, key: str):
        frame = ttk.Frame(parent)
        frame.header = ttk.Frame(frame)
        frame.header.pack(fill="x")
        ttk.Label(frame.header, text=title, style="Section.TLabel").pack(side="left")
        if key in self.font_sizes:
            self._font_controls(frame.header, key, command=lambda key=key: self._apply_font(key)).pack(side="right")
        frame.body = ttk.Frame(frame)
        frame.body.pack(fill="both", expand=True, pady=(4, 0))
        return frame

    def _font_controls(self, parent: tk.Widget, key: str, command) -> ttk.Frame:
        frame = ttk.Frame(parent)
        ttk.Button(frame, text="-", width=2, command=lambda: self._change_font_size(key, -1, command)).pack(side="left")
        ttk.Label(frame, textvariable=self.font_sizes[key], width=2, anchor="center").pack(side="left")
        ttk.Button(frame, text="+", width=2, command=lambda: self._change_font_size(key, 1, command)).pack(side="left")
        return frame

    def _make_vocab_tree(self, parent: tk.Widget, selectmode: str, include_time: bool = False) -> ttk.Treeview:
        columns = ("expression", "reading", "meaning", "created_at") if include_time else ("expression", "reading", "meaning")
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode=selectmode, style="Vocab.Treeview")
        headings = {"expression": "单词", "reading": "读音", "meaning": "意思", "created_at": "时间"}
        widths = {"expression": 110, "reading": 110, "meaning": 180, "created_at": 120}
        for key in columns:
            tree.heading(key, text=headings[key])
            tree.column(key, width=widths[key], anchor="w")
        return tree

    def _change_font_size(self, key: str, delta: int, command) -> None:
        self.font_sizes[key].set(max(8, min(30, self.font_sizes[key].get() + delta)))
        command()

    def _apply_font(self, key: str) -> None:
        size = self.font_sizes[key].get()
        if key == "original":
            self.original_text.configure(font=("Meiryo", size))
        elif key == "furigana":
            self.furigana_view.set_font_size(size)
        elif key == "translation":
            self.translation_text.configure(font=("Microsoft YaHei UI", size))
        elif key == "grammar":
            self.grammar_text.configure(font=("Microsoft YaHei UI", size))
        elif key == "vocab":
            rowheight = max(28, int(size * 2.6))
            ttk.Style(self).configure("Vocab.Treeview", font=("Meiryo", size), rowheight=rowheight)

    def _initial_status(self) -> str:
        if self.analyzer.is_configured:
            return f"已连接 BigModel，模型：{self.analyzer.model}"
        return "未设置 BIGMODEL_API_KEY；可先使用界面，设置后启用识别和分析"

    def select_region(self) -> None:
        self.withdraw()
        self.after(250, lambda: RegionSelector(self, self.capture_region))

    def capture_region(self, bbox: tuple[int, int, int, int], analyze: bool = False) -> None:
        self.deiconify()
        image = ImageGrab.grab(bbox=bbox)
        image.save(LAST_SCREENSHOT)
        LAST_REGION.write_text(json.dumps(list(bbox)), encoding="utf-8")
        self.show_preview(LAST_SCREENSHOT)
        self.status_var.set(f"已截取区域：{bbox}")
        if analyze:
            self.analyze_screenshot()

    def rescan_last_region(self) -> None:
        bbox = self._load_last_region()
        if bbox is None:
            messagebox.showinfo("还没有上次区域", "请先点击“选择文字区域”框选一次。")
            return
        self.capture_region(bbox, analyze=True)

    def _load_last_region(self) -> tuple[int, int, int, int] | None:
        if not LAST_REGION.exists():
            return None
        try:
            values = json.loads(LAST_REGION.read_text(encoding="utf-8"))
            if len(values) != 4:
                return None
            return tuple(int(value) for value in values)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def show_preview(self, image_path: Path) -> None:
        image = Image.open(image_path)
        image.thumbnail((760, 170))
        self.preview_image = ImageTk.PhotoImage(image)
        self.preview_label.configure(image=self.preview_image, text="")

    def open_image(self) -> None:
        path = filedialog.askopenfilename(
            title="选择日语截图",
            filetypes=[("Image files", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"), ("All files", "*.*")],
        )
        if not path:
            return
        image = Image.open(path).convert("RGB")
        image.save(LAST_SCREENSHOT)
        self.show_preview(LAST_SCREENSHOT)
        self.status_var.set("已载入截图")

    def analyze_screenshot(self) -> None:
        if not LAST_SCREENSHOT.exists():
            messagebox.showinfo("还没有截图", "请先选择文字区域，或打开一张截图。")
            return
        self._run_analysis(lambda: self.analyzer.analyze_image(LAST_SCREENSHOT), "正在识别截图...")

    def analyze_manual_text(self) -> None:
        text = self.original_text.get("1.0", "end").strip()
        self._run_analysis(lambda: self.analyzer.analyze_text(text), "正在分析文本...")

    def speak_original(self) -> None:
        text = self.original_text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("没有原文", "请先识别或输入日语原文。")
            return
        TTS_TEXT_PATH.write_text(text, encoding="utf-8")
        path = str(TTS_TEXT_PATH).replace("'", "''")
        script = f"""
Add-Type -AssemblyName System.Speech
$text = Get-Content -LiteralPath '{path}' -Raw -Encoding UTF8
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {{
  $s.SelectVoice('Microsoft Ichiro')
}} catch {{
  try {{
    $culture = New-Object System.Globalization.CultureInfo('ja-JP')
    $s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Male, [System.Speech.Synthesis.VoiceAge]::Adult, 0, $culture)
  }} catch {{
    try {{
      $culture = New-Object System.Globalization.CultureInfo('ja-JP')
      $s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::NotSet, [System.Speech.Synthesis.VoiceAge]::Adult, 0, $culture)
    }} catch {{}}
  }}
}}
$s.Rate = -1
$s.Speak($text)
"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as error:
            messagebox.showerror("朗读失败", f"无法启动 Windows 语音朗读：{error}")

    def _run_analysis(self, task, status: str) -> None:
        self.status_var.set(status)
        self._set_buttons_state("disabled")

        def worker() -> None:
            try:
                result = task()
            except AnalyzerError as error:
                self.after(0, lambda: self._show_error(str(error)))
            except Exception as error:
                self.after(0, lambda: self._show_error(f"发生未知错误：{error}"))
            else:
                self.after(0, lambda: self.display_result(result))
            finally:
                self.after(0, lambda: self._set_buttons_state("normal"))

        threading.Thread(target=worker, daemon=True).start()

    def _set_buttons_state(self, state: str) -> None:
        for child in self.winfo_children():
            self._walk_buttons(child, state)

    def _walk_buttons(self, widget, state: str) -> None:
        if isinstance(widget, ttk.Button):
            widget.configure(state=state)
        for child in widget.winfo_children():
            self._walk_buttons(child, state)

    def _show_error(self, message: str) -> None:
        self.status_var.set("处理失败")
        messagebox.showerror("处理失败", message)

    def display_result(self, result: AnalysisResult) -> None:
        self.current_result = result
        self._replace_text(self.original_text, result.original_text)
        self.furigana_view.set_text(self._build_furigana_display(result))
        self._replace_text(self.translation_text, result.translation_zh)
        grammar_lines = []
        if result.grammar:
            grammar_lines.append("【语法】")
        for item in result.grammar:
            example_ja = item.get("new_example_ja", "") or item.get("example_ja", "")
            example_zh = item.get("new_example_zh", "") or item.get("example_zh", "")
            line = f"{item.get('pattern', '')}\n{item.get('explanation_zh', '')}"
            if example_ja:
                line += f"\n新例句：{example_ja}"
            if example_zh:
                line += f"\n例句翻译：{example_zh}"
            grammar_lines.append(line)
        if result.collocations:
            grammar_lines.append("【固定搭配 / 惯用表达】")
        for item in result.collocations:
            example_ja = item.get("new_example_ja", "") or item.get("example_ja", "")
            example_zh = item.get("new_example_zh", "") or item.get("example_zh", "")
            head = item.get("expression", "")
            reading = item.get("reading", "")
            if reading:
                head += f"（{reading}）"
            line = f"{head}\n{item.get('meaning_zh', '')}"
            usage = item.get("usage_zh", "")
            if usage:
                line += f"\n用法：{usage}"
            if example_ja:
                line += f"\n新例句：{example_ja}"
            if example_zh:
                line += f"\n例句翻译：{example_zh}"
            grammar_lines.append(line)
        self._replace_text(self.grammar_text, "\n\n".join(grammar_lines))
        self._fill_detected_vocab(result.vocabulary)
        self.status_var.set("分析完成")

    def _build_furigana_display(self, result: AnalysisResult) -> str:
        text = result.furigana_text.strip() or result.original_text
        annotated = self._annotate_from_vocabulary(text, result.vocabulary)
        if KANJI_RE.search(re.sub(KANJI_GROUP_RE, "", annotated)):
            annotated = self._annotate_remaining_kanji(annotated)
        return annotated

    def _annotate_from_vocabulary(self, text: str, vocabulary: list[dict[str, str]]) -> str:
        words = sorted(vocabulary, key=lambda item: len(item.get("expression", "")), reverse=True)
        for word in words:
            expression = word.get("expression", "").strip()
            reading = word.get("reading", "").strip()
            if not expression or not reading or not KANJI_RE.search(expression):
                continue
            replacement = self._annotate_expression(expression, reading)
            if replacement == expression:
                continue
            text = self._replace_unannotated(text, expression, replacement)
        return text

    def _annotate_expression(self, expression: str, reading: str) -> str:
        if not KANJI_RE.search(expression):
            return expression
        if KANA_RE.fullmatch(reading) is None:
            return f"{expression}({reading})"

        match = re.match(r"^([\u3400-\u9fff\u3005\u3006\u30f6]+)([\u3040-\u309f\u30a0-\u30ffー]*)$", expression)
        if match:
            kanji, okurigana = match.groups()
            kanji_reading = reading
            if okurigana and reading.endswith(okurigana):
                kanji_reading = reading[: -len(okurigana)]
            if kanji_reading:
                return f"{kanji}({kanji_reading}){okurigana}"

        pieces = []
        last = 0
        for group in re.finditer(r"[\u3400-\u9fff\u3005\u3006\u30f6]+", expression):
            pieces.append(expression[last : group.start()])
            pieces.append(f"{group.group(0)}(?)")
            last = group.end()
        pieces.append(expression[last:])
        return "".join(pieces)

    def _replace_unannotated(self, text: str, expression: str, replacement: str) -> str:
        result = []
        pos = 0
        for annotated in KANJI_GROUP_RE.finditer(text):
            plain = text[pos : annotated.start()]
            result.append(plain.replace(expression, replacement))
            result.append(annotated.group(0))
            pos = annotated.end()
        result.append(text[pos:].replace(expression, replacement))
        return "".join(result)

    def _annotate_remaining_kanji(self, text: str) -> str:
        result = []
        pos = 0
        for match in KANJI_GROUP_RE.finditer(text):
            result.append(KANJI_RE.sub(lambda kanji: f"{kanji.group(0)}(?)", text[pos : match.start()]))
            result.append(match.group(0))
            pos = match.end()
        result.append(KANJI_RE.sub(lambda kanji: f"{kanji.group(0)}(?)", text[pos:]))
        return "".join(result)

    def _replace_text(self, widget: tk.Text, value: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", value)

    def _fill_detected_vocab(self, words: list[dict[str, str]]) -> None:
        for item in self.vocab_tree.get_children():
            self.vocab_tree.delete(item)
        for index, word in enumerate(words):
            self.vocab_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(word.get("expression", ""), word.get("reading", ""), word.get("meaning_zh", "")),
            )

    def _on_double_click_vocab(self, event) -> None:
        row_id = self.vocab_tree.identify_row(event.y)
        if not row_id:
            return
        index = int(row_id)
        saved = self._save_word_index(index, show_message=False)
        try:
            word_expr = self.current_result.vocabulary[index].get("expression", "")
            if saved:
                self.status_var.set(f"已快捷收藏单词：{word_expr}")
            else:
                self.status_var.set(f"单词已存在：{word_expr}")
        except IndexError:
            pass

    def _on_double_click_saved(self, event) -> None:
        row_id = self.saved_tree.identify_row(event.y)
        if not row_id:
            return
        try:
            db_id = int(row_id)
            item_data = self.saved_tree.item(row_id)
            word_expr = item_data.get("values", [""])[0]
            if self.store.delete(db_id):
                self.refresh_vocab()
                self.status_var.set(f"已快捷删除单词：{word_expr}")
        except (ValueError, IndexError, TypeError, KeyError):
            pass

    def save_selected_word(self) -> None:
        selection = self.vocab_tree.selection()
        if not selection:
            messagebox.showinfo("未选择单词", "请先在上方列表选择要收藏的单词（支持按住 Ctrl 或 Shift 多选）。")
            return

        saved_count = 0
        duplicate_count = 0

        for item_id in selection:
            if self._save_word_index(int(item_id), show_message=False):
                saved_count += 1
            else:
                duplicate_count += 1

        self.refresh_vocab()

        if len(selection) == 1:
            messagebox.showinfo("收藏结果", "已收藏。" if saved_count == 1 else "这个词已经收藏过了。")
        else:
            msg = f"成功收藏 {saved_count} 个单词。"
            if duplicate_count > 0:
                msg += f"\n（另有 {duplicate_count} 个单词已存在于单词本中）"
            messagebox.showinfo("收藏结果", msg)

    def save_all_words(self) -> None:
        count = 0
        for index in range(len(self.current_result.vocabulary)):
            if self._save_word_index(index, show_message=False):
                count += 1
        self.refresh_vocab()
        messagebox.showinfo("已收藏", f"新增收藏 {count} 个单词。")

    def _save_word_index(self, index: int, show_message: bool = True) -> bool:
        try:
            word = self.current_result.vocabulary[index]
        except IndexError:
            return False
        saved = self.store.add(
            VocabEntry(
                expression=word.get("expression", ""),
                reading=word.get("reading", ""),
                meaning=word.get("meaning_zh", ""),
                sentence=self.current_result.original_text,
                source="visual novel",
            )
        )
        self.refresh_vocab()
        if show_message:
            messagebox.showinfo("收藏结果", "已收藏。" if saved else "这个词已经收藏过了。")
        return saved

    def delete_saved_word(self) -> None:
        selection = self.saved_tree.selection()
        if not selection:
            messagebox.showinfo("未选择单词", "请先在单词本里选择一个单词。")
            return
        deleted = self.store.delete(int(selection[0]))
        self.refresh_vocab()
        messagebox.showinfo("删除结果", "已删除。" if deleted else "没有找到这条记录。")

    def refresh_vocab(self) -> None:
        for item in self.saved_tree.get_children():
            self.saved_tree.delete(item)
        for row in self.store.list_all():
            self.saved_tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(row["expression"], row["reading"], row["meaning"], row["created_at"]),
            )

    def export_csv(self) -> None:
        path = OUTPUT_DIR / "vocabulary.csv"
        self.store.export_csv(path)
        messagebox.showinfo("导出完成", f"已导出到：\n{path}")


if __name__ == "__main__":
    app = VNJapaneseTool()
    app.mainloop()
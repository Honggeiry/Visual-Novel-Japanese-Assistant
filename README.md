# 视觉小说日语助手

这是一个 Windows 桌面小工具原型，用来辅助阅读 Steam 视觉小说里的日语文本。

## 当前功能

- 框选屏幕上的游戏文字区域并截图
- 打开已有截图
- 使用智谱 BigModel 分析截图或文本，并生成：
  - 原文
  - 汉字假名标注
  - 中文翻译
  - 重点语法
  - 重点单词
- 手动粘贴日文后分析
- 收藏单词到本地 SQLite 单词本
- 导出单词本 CSV

## 运行

在项目根目录运行：

```powershell
.\run_vn_jp_tool.ps1
```

或者双击：

```text
run_vn_jp_tool.bat
```

## BigModel 配置

本项目会读取项目根目录的 `.env` 文件：

```text
BIGMODEL_API_KEY=你的智谱 API key
BIGMODEL_MODEL=GLM-4.6V-FlashX
```

当前已按你的要求配置为 `GLM-4.6V-FlashX`。

## 使用方式

1. 打开视觉小说，让文字显示在屏幕上。
2. 点击“选择文字区域”，拖动框选对白框。
3. 回到工具后点击“识别截图并分析”。
4. 在右侧选择想复习的单词，点击“收藏选中单词”。
5. 需要备份或导入 Anki 时，点击“导出 CSV”。

## 数据位置

- 截图缓存：`work/vn_jp_tool_data/last_capture.png`
- 单词本数据库：`work/vn_jp_tool_data/vocabulary.sqlite3`
- CSV 导出：`outputs/vn_jp_tool/vocabulary.csv`
"# Visual-Novel-Japanese-Assistant" 

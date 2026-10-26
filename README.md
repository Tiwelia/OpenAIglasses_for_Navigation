# ESP32-S3 图像与语音采集课程项目

基于XIAO ESP32S3 Sense的综合实践原型：真实板端采集图像和声音，电脑运行后端，浏览器负责取景与交互。

从 [AI-FanGe/OpenAIglasses_for_Navigation](https://github.com/AI-FanGe/OpenAIglasses_for_Navigation) 精简而来，保留原MIT许可证。当前不提供盲人导航或安全通行判断。

## 当前进度

| 功能 | 状态 |
| --- | --- |
| USB摄像头预览、网页旋转和手动截图 | 已实机验证 |
| 2.4GHz手机热点下Wi-Fi预览 | 短时实测VGA约3—5fps |
| PDM麦克风→USB→5秒WAV | 已实机校验并试听 |
| 单次语音转写与图片问答API | 已用硬件样本验证 |
| 网页长录音、无线音频、多轮图像语音问答 | 待集成 |
| 断网恢复、30分钟稳定性、跨设备访问 | 待完整验证 |

三个固件独立，同一时刻只能烧录一个。麦克风测试固件不会提供摄像头画面。

## 安装

Python 3.11，在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item course/.env.example course/.env
$env:COURSE_AI_ENABLED='0'
.\.venv\Scripts\python.exe -B -m course.app
```

打开 <http://127.0.0.1:8081/preview>。按[运行指南](docs/public/SETUP.md)烧录并启动对应采集端才有图像。预览和普通截图不调用AI；云端探针必须显式传入`--send`才会上传指定文件并可能收费。

## 目录

- `course/`：FastAPI后端、浏览器界面、USB桥接、录音接收和模型探针。
- `course/firmware/`：USB摄像头、Wi-Fi摄像头、USB麦克风三种固件。
- `docs/public/`：可复现运行步骤与验证限制。
- `scripts/audit_public.py`：暂存内容与历史敏感项检查。

旧导航代码、权重、Python环境、照片录音、数据库、真实配置和本机交接笔记不进入当前提交。可复用资源保留在开发者本地归档；原实现也可从上游查阅。

## 验证

```powershell
.\.venv\Scripts\python.exe -B -m unittest course.test_app -q
.\.venv\Scripts\python.exe -B scripts/audit_public.py
```

单元测试使用临时数据和模拟模型，不收费。详见[验证说明](docs/public/VALIDATION.md)。

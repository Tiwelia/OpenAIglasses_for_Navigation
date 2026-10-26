# 硬件与运行

需要Python 3.11、Arduino CLI/IDE、esp32核心及XIAO ESP32S3 Sense。实测核心3.3.12；Wi-Fi固件另需ArduinoWebsockets 0.5.4。摄像头与ESP_I2S库由核心提供。

下例假设arduino-cli已加入PATH，也可替换为IDE附带CLI完整路径。先安装esp32核心，再安装库并查看端口：

```powershell
arduino-cli lib install 'ArduinoWebsockets@0.5.4'
arduino-cli board list
```

## 固件选择

| 目录 | 用途 |
| --- | --- |
| course/firmware/camera_check | USB摄像头：I状态、C拍照、V预览 |
| course/firmware/wifi_camera | Wi-Fi JPEG上传，最高5fps |
| course/firmware/usb_mic_check | USB麦克风：I状态、R录5秒 |

烧录替换板内应用。以下以麦克风为例，COM5仅示例，应按实际端口替换：

```powershell
arduino-cli compile --fqbn 'esp32:esp32:XIAO_ESP32S3:PSRAM=opi,CDCOnBoot=default,USBMode=hwcdc,FlashSize=8M' course/firmware/usb_mic_check
arduino-cli upload --port COM5 --fqbn 'esp32:esp32:XIAO_ESP32S3:PSRAM=opi,CDCOnBoot=default,USBMode=hwcdc,FlashSize=8M' course/firmware/usb_mic_check
```

编译成功后才上传，关闭其他串口占用。在实测核心中CDCOnBoot=default代表启用。

## USB预览

烧录camera_check，启动后端，在另一终端运行：

```powershell
.\.venv\Scripts\python.exe -B -m course.usb_camera --device COM5 --fps 10
```

网页`/preview`可旋转、截图、下载。RESET/BOOT不是拍照按钮。

## Wi-Fi预览

复制wifi_camera目录的`secrets.example.h`为`secrets.h`，填写Wi-Fi名称、密码、电脑局域网IP，再编译烧录。需要2.4GHz网络；iPhone热点开启最大兼容性。手机热点/普通路由器下`CAMERA_SERVER_HOST`必须填电脑IP，不能填网关。仅电脑自己开热点时可留空使用网关地址。

停止USB桥接，只启动后端。板子可用USB充电器供电，以镜头实际动作、变化的帧序号及transport=wifi确认传输。电脑地址变化后需更新配置重新烧录。

## USB麦克风

烧录usb_mic_check后：

```powershell
.\.venv\Scripts\python.exe -B -m course.usb_mic --device COM5 --output course/data/mic-test.wav
```

生成原始WAV、统计JSON及去直流放大的试听副本，不覆盖已有文件。PDM时钟42、数据41，16kHz/16位单声道。录音时不启动摄像头，不持续监听，不调用AI。

## AI单次验证

在course/.env填写密钥、对应地域Base URL及模型。首版实测ASR为qwen3-asr-flash，视觉为qwen3.8-flash。保留`COURSE_AI_ENABLED=0`，普通预览不调用AI。

```powershell
# 不带--send只检查输入；加--send会上传并可能收费。
.\.venv\Scripts\python.exe -B -m course.probe_ai audio --file course/data/mic-test-listen.wav --output course/data/asr-result.json --send
.\.venv\Scripts\python.exe -B -m course.probe_ai image --file course/data/example.jpg --output course/data/vision-result.json --send
```

图片需自行准备，仓库无个人实拍。探针为录完识别、非流式返回，不是实时语音聊天。密钥只放电脑后端，不放网页或固件。

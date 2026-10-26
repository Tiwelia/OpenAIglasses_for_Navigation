# 当前课程版

安装与硬件步骤见[运行指南](../docs/public/SETUP.md)，验证边界见[验证说明](../docs/public/VALIDATION.md)。程序不导入旧导航、YOLO、IMU或旧播放器依赖。

- 图像：ESP32 JPEG → `/ws/camera?transport=usb|wifi` → `/ws/viewer` → `/preview`。
- 只允许一个采集端上行，USB桥接和Wi-Fi不要同时抢占连接。
- 慢观看者只保留最新帧；失联显示最后帧并禁用截图。
- 网页截图在浏览器内存中，须下载保存；刷新会丢失，不会自动进入数据库。
- USB麦克风测试：显式命令录5秒，接收PCM写WAV，尚未集成网页录音。
- `probe_ai.py`默认仅检查输入；`--send`才调用一次云端，独立于网页AI开关。
- `/api/analyze`仍取服务端当前帧，尚未完成固定截图会话；保留网页AI关闭。

配置优先级：进程环境 > `course/.env` > 根目录`.env`。复制`.env.example`配置本机，不提交实际配置。运行数据在被忽略的`course/data/`。

服务监听`0.0.0.0:8081`，用于受控开发局域网，尚无鉴权，不直接暴露公网。跨设备访问需实测防火墙、热点隔离和代理。

`send_video.py`仅供标注来源的录像/电脑摄像头调试，需另装OpenCV，其结果不作为ESP32硬件证据。

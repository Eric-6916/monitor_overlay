一个 Windows 桌面悬浮监控小组件，提供：

CPU 使用率 + 趣味文案

GPU 使用率 + 趣味文案

RAM 使用率 + 趣味文案

DISK 忙碌度（任务管理器同款“活动时间 %”）+ 趣味文案（跨档稳定）

网络上传/下载速率（带单位）

安装与运行

1 创建虚拟环境（venv）

在项目根目录执行：

py -m venv venv

venv\Scripts\activate

2 安装依赖

pip install -r requirements.txt

3 打包

pyinstaller --onefile --noconsole --name MonitorOverlay main.py --add-binary "LibreHardwareMonitorLib\LibreHardwareMonitorLib.dll;LibreHardwareMonitorLib"

# 知见新版分发说明

分享 `dist/zhijian-windows.zip`，不要只发送 exe。解压后静默启动器为 zhijian_silent.exe，控制台启动器为 zhijian.exe，浏览器组件在 _internal 中，必须保留。

发布包必须不含 data、浏览器个人会话、Cookie、Token、数据库和日志。不要分享自己正在使用的程序文件夹；应使用打包脚本输出的干净压缩包。

对方首次使用在设置里点击连接平台，通过官方窗口完成扫码登录。各自使用自己的账号和数据，不需要你提供凭据。登录过期仍需要重新连接。淘股吧公开内容免登录。

源码分享同样排除 data、.venv、browsers、__pycache__、构建目录，接收者运行 install.bat 下载依赖和浏览器。

升级请先关闭旧程序并保留 data 备份。停止静默版可在任务管理器结束 zhijian_silent.exe；设置开机自启可使用 exe_autostart_on.bat，取消使用 exe_autostart_off.bat。

请勿承诺公众号或其他平台不会漏帖。本版不补历史，不监控淘股吧跟帖，也不支持公网多人服务。

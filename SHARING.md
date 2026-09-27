# 知见 · 分发指南（给你自己看的）

## ⚠️ 第一原则：永远不要把 `data` 文件夹给任何人

`data/config.json` 里有你的微博 Cookie，等于你的账号密码。分发前检查三遍。

## 方式一：发 exe（推荐给非技术朋友）

两个版本按需选：
- **常规版**：双击 `build_exe.bat` → `dist\zhijian.exe`。运行时有个黑窗口，好处是报错可见
- **无窗口版**：双击 `build_exe_silent.bat` → `dist\zhijian_silent.exe`。完全静默运行，
  适合追求无感的朋友。日志写在 exe 旁边的 `data\run.log`（出问题让他发这个文件给你），
  停止程序要在任务管理器里结束 `zhijian_silent.exe`

朋友那边：双击 exe → 浏览器打开 http://127.0.0.1:8000 → 点设置填自己的 Cookie、加自己想监控的人
（常规版会多个黑窗口，别关；无窗口版则完全无感）。
**朋友的数据存在他自己电脑上 exe 旁边的 data 文件夹里，和你完全无关。**
无窗口版想开机自启：把 `exe_autostart_on.bat`、`exe_autostart_off.bat` 两个文件和 exe 一起发给朋友，
朋友双击 `exe_autostart_on.bat` 即可（取消则双击 off 那个）。
也可以手动：Win+R 输入 `shell:startup`，把 exe 快捷方式拖进去。
（自启通过注册表 HKCU Run 键实现，不需要管理员权限）

注意：
- 部分杀毒软件会对 PyInstaller 打的包误报，让朋友加白名单即可（这是单文件 exe 的通病，不是病毒）
- exe 是你电脑当前代码的快照，以后程序更新了要重新打包再发一次

## 方式二：发源码 zip（给愿意折腾的朋友）

1. 把整个项目文件夹打成 zip（**删掉 data 文件夹后再打！**）
2. 朋友解压后：装 Python 3.10+ → 双击 `install.bat` → 双击 `start.bat`

## 朋友必须自己做的事（谁都替不了）

- **微博 Cookie**：让他的小号登录 m.weibo.cn，F12 拿 Cookie（教程在 README）。用谁的账号抓就是谁的账号承担风控，这点务必跟朋友讲清楚
- **淘股吧**：不用登录，什么都不用准备
- **知识星球**：想监控的话要自己的 zsxq_access_token（教程在 README），只读免费星球也需要登录态

## 常见问题

| 朋友的现象 | 原因与解法 |
|---|---|
| 双击 exe 闪退 | 让他用命令行跑 exe 看报错，截图给你 |
| 微博一直"凭证失效" | Cookie 没填或过期，重新粘贴 |
| 杀毒软件报毒 | PyInstaller 误报，加白名单 |
| 想监控的人加不上 | 检查用户 ID 格式（微博是数字 uid，淘股吧是 blog 后的数字） |

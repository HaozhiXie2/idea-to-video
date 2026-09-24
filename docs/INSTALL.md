# 安装与运行

## 系统与准备

首版启动器针对 Windows 10/11 x64。安装 [Python 3.12 或更新版本](https://www.python.org/downloads/windows/)；安装时可勾选加入 PATH。日常使用不需要 Node.js、CUDA、本地绘图模型或原小说项目。

建议独立目录：`C:\Users\Administrator\Documents\ChatGPT\灵感影坊`。从 GitHub 下载或克隆时，只放新项目的文件，不复制旧项目的 `.git`、`.local`、`.tools` 或素材目录。

## 1. 安装本项目运行环境

在项目根目录打开 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install.ps1
```

如果没有检测到 Python，显式指定系统上的解释器：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install.ps1 -Python "C:\Path\To\Python312\python.exe"
```

安装器会：

- 新建本项目的 `.local/venv`，不借用原项目虚拟环境。
- 安装固定版本 `imageio-ffmpeg==0.6.0`，其 Windows wheel 包含 FFmpeg。版本和平台二进制信息可在 [PyPI 官方发布页](https://pypi.org/project/imageio-ffmpeg/0.6.0/)核对。
- 从即梦官方安装页列出的 HTTPS 下载地址获取 Windows x64 CLI，放入 `.tools/dreamina.exe`；不执行远程安装脚本，不修改系统 PATH，不复制任何旧授权。
- 保留已有 CLI，不自动升级。安装时显示的 SHA256 是本地下载记录，不等于厂商签名验证；官方当前安装页未提供该二进制的独立签名校验清单。

仅做离线开发可以加 `-SkipDreamina`。安装、登录本身不提交图片或视频生成请求，但联网安装会下载第三方程序。网络不通时可以稍后重跑；请不要从不明镜像下载替代可执行文件。

## 2. 独立授权即梦

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\login.ps1
```

根据终端打印的官方网址与设备码，在浏览器里完成授权。设备码、登录凭据不要截图分享或提交到 GitHub；如果平台要求接受协议，应由你在官方页面阅读并决定。

登录只写本项目 `.local/dreamina-home`，不会自动复用原小说项目的登录。即使授权的是同一个账号，平台的额度、会员权益和并发限制仍然共享。

## 3. 启动与配置文字服务

双击根目录的 **Start-Studio.cmd**。它会在后台启动本机服务，并打开 `http://127.0.0.1:7862/`。开发者也可以使用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-studio.ps1 -NoBrowser
```

首次在页面设置中配置 Chat Completions 兼容服务的地址、模型名和 API 密钥。服务地址必须使用 HTTPS，本机开发服务除外。请按提供商说明填写接口基础地址。密钥只写后端本地配置；页面以后只显示是否已设置，不返回明文密钥。保存配置不会自动发送收费测试请求。

没有配置文字服务时仍能打开页面，但不能完成真实创作分析。即梦会员、即梦登录、文字服务 API 三者不是一回事；需要单独核实各服务权益。

## 运行与故障

- **7862 已被占用：** 启动器只复用身份为 `idea-to-video` 的服务，不结束或覆盖其他程序。检查占用的程序后自行处理，不要关闭原小说项目的 7861 工作台。
- **打不开页面：** 查看 `.local/studio-error.log`。不要把可能包含用户输入的完整日志公开贴到仓库；先脱敏。
- **即梦需要重新授权：** 重新运行 `scripts/login.ps1`，不重新提交已有生成任务。平台协议、风控或账号限制需要在官方页面处理。
- **生成请求结果不明：** 保留任务，先查平台已有任务。不要反复点击新建来碰运气；这可能重复扣费。只有确认已有任务编号后才进行查询恢复。
- **导出不可用：** 检查是否安装了本项目 FFmpeg、是否所有当前镜头已生成并审核。静态草稿与动态成片是两类不同导出。
- **关闭网页：** 不等于停止后台服务或取消云端任务。下次打开可恢复查看；先在页面暂停后续提交再关闭电脑。

本项目不会因为重启就自动重发不确定的收费请求；已在云端的任务可能仍会继续并消耗额度。

## 备份与公开仓库

代码可以公开，`.local/` 与 `data/` 不要公开。作品、任务与版本位于 `data/projects/`；文字配置位于 `.local/config.json`；即梦授权位于 `.local/dreamina-home/`。备份作品时，在工作台无正在写入的任务时，将自己的 `data/` 安全复制到私人备份位置；本地配置可能含密钥，需单独加密保管。不要用 `git add -f` 绕过忽略规则。

公开发布前运行 `python scripts/check-repo.py`，但自动检查不能保证发现所有秘密；发布者仍应检查变更列表与截图。

## 下载来源

- [即梦官方创作 CLI 安装页](https://jimeng.jianying.com/cli)
- [imageio-ffmpeg 0.6.0 官方包](https://pypi.org/project/imageio-ffmpeg/0.6.0/)
- [Python 官方 Windows 下载](https://www.python.org/downloads/windows/)

安装器不执行官方安装页返回的 shell 脚本，只使用其中的 Windows 二进制下载地址。若上游地址或 CLI 行为改变，应先核对官方说明再修改适配层；不要在排错时自动换成未知代理服务。

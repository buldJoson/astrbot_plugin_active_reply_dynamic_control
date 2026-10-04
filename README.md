# 主动回复动态控制 (Active Reply Dynamic Control)

一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件：按**时间段**与**模式**动态控制机器人对群聊的回复行为。仅对群聊生效，私聊不受影响。

## 功能

- **定时封锁**：每天 / 自定义星期 / 不重复（指定日期）三种时间段类型，支持跨午夜时间窗（如 22:00–02:00）；时间段为可选项，**未配置时默认全天封锁**。
- **封锁模式**：
  - 完全封锁——封锁时段内不回复任何消息；
  - 仅被@回复——仅当消息 @ 机器人 / 触发唤醒词时回复，其余静默。
- **WebUI 管理面板**（在 AstrBot 插件详情页打开，自适应浅色 / 深色）：
  - 总览——已添加群聊的封锁状态与启停滑块；
  - 行为设置——通过「＋ 添加群聊」弹窗添加群聊（添加后默认启用），配置封锁模式与多个时间段；
  - 系统——全局总开关、豁免规则、封锁提示方式与文案、日志级别、一键重置。
- **豁免规则（可配置）**：管理员消息豁免、以 `/` 开头的系统指令豁免。
- **封锁提示**：静默 / @发送者 / 群内公开三种方式，提示文案自定义。
- **群内查询**：在群里发送 `/封锁状态`，即时查看本群的封锁配置与当前生效状态。
- **高优先级拦截**：事件监听器以最高优先级（`sys.maxsize - 1`）注册，在任何回复产生前完成封锁判定。

## 安装

1. 将 `astrbot_plugin_active_reply_dynamic_control` 文件夹放入 AstrBot 的 `data/plugins/` 目录；
2. 重启 AstrBot，或在 WebUI「插件」页启用 `active_reply_dynamic_control`；
3. 在左侧「插件 WebUI」分组中打开「主动回复动态控制」面板开始配置。

无额外 Python 依赖。

## 配置存储

所有配置持久化于 `data/plugin_data/astrbot_plugin_active_reply_dynamic_control/config.json`，插件重载或重启后不会丢失。

## 支持平台

aiocqhttp（OneBot v11 / NapCat / Lagrange 等 QQ 协议端）。

## 开源协议

本项目基于 [MIT License](LICENSE) 开源。

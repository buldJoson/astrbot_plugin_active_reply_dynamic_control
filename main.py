# main.py
# 主动回复动态控制 (Active Reply Dynamic Control)
# 作者: buldJoson
#
# 功能:
#   1. WebUI 设置页 (Plugin Page: pages/admin/index.html)
#   2. 定时封锁: 每天 / 自定义星期 / 不重复(指定日期), 支持跨午夜时间窗
#   3. 模式选择: 1) 完全封锁  2) 被@回复
#   4. 左侧三栏: 总览 / 行为设置 / 系统
#
# 说明: 本插件使用 AstrBot 的 Plugin Pages 机制提供自定义面板,
#       所有配置持久化在插件数据目录下的 config.json 中。

import json
import sys
import uuid
from pathlib import Path
from datetime import datetime

from astrbot.api.star import Context, Star
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
from astrbot.api.web import json_response, error_response, request
from astrbot.api.message_components import Plain, At
from astrbot import logger
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

PLUGIN_NAME = "astrbot_plugin_active_reply_dynamic_control"
# 插件文件夹名 (路由前缀候选之一; WebUI bridge 实际使用元数据 name, 见 _resolve_route_prefixes)
PLUGIN_DIR_NAME = Path(__file__).resolve().parent.name

# 模式
MODE_BLOCK_ALL = "block_all"      # 完全封锁
MODE_REPLY_AT = "reply_at"        # 被@回复
# 提示方式
NOTICE_NONE = "none"              # 静默
NOTICE_AT = "at_sender"           # @发送者提示
NOTICE_GROUP = "group_notice"     # 群内公开提示

DEFAULT_DATA = {
    "global": {
        "enabled": True,          # 插件总开关
        "bypass_admins": True,    # 管理员豁免
        "bypass_commands": True,  # 系统指令(/开头)豁免
        "log_level": "INFO",      # 日志级别 (DEBUG 时输出放行原因)
        "notice_mode": NOTICE_NONE,  # 被封锁时的提示方式
        "notice_text": "当前时段机器人已暂停主动回复，被@时仍会回应。",
    },
    "groups": {},  # group_id -> {group_id, group_name, enabled, mode, schedules:[...]}
}


class ActiveReplyDynamicControl(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context

        # 数据目录 (插件专属, 安全持久化)
        self.data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.data_file = self.data_dir / "config.json"
        self.data = self._load_data()

        # 注册 Web API (供 Plugin Page 前端调用)
        # 路由前缀必须与 WebUI bridge 请求的 /api/plug/<前缀>/... 中的 <前缀> 完全一致。
        # dashboard 使用插件元数据 name 作为前缀, 因此对每个候选前缀都注册一遍
        # (路由为精确匹配, 多前缀注册互不冲突)。
        ctx = self.context
        for prefix in self._resolve_route_prefixes():
            p = f"/{prefix.strip('/')}/api"
            ctx.register_web_api(f"{p}/overview", self.api_overview, ["GET"], "总览数据")
            ctx.register_web_api(f"{p}/groups", self.api_groups, ["GET"], "待添加群列表")
            ctx.register_web_api(f"{p}/controlled", self.api_controlled, ["GET"], "已添加群列表")
            ctx.register_web_api(f"{p}/group/add", self.api_group_add, ["POST"], "添加受控群")
            ctx.register_web_api(f"{p}/group/remove", self.api_group_remove, ["POST"], "移除受控群")
            ctx.register_web_api(f"{p}/group/toggle", self.api_group_toggle, ["POST"], "切换群开关")
            ctx.register_web_api(f"{p}/group/mode", self.api_group_mode, ["POST"], "设置群模式")
            ctx.register_web_api(f"{p}/schedule/add", self.api_schedule_add, ["POST"], "添加时间段")
            ctx.register_web_api(f"{p}/schedule/remove", self.api_schedule_remove, ["POST"], "移除时间段")
            ctx.register_web_api(f"{p}/settings", self.api_settings, ["GET", "POST"], "全局设置")
            ctx.register_web_api(f"{p}/reset", self.api_reset, ["POST"], "重置数据")

    # ------------------------------------------------------------------
    # Web API 路由前缀解析
    # ------------------------------------------------------------------
    def _resolve_route_prefixes(self) -> list:
        """解析可用于注册的插件标识前缀。

        WebUI 侧 (PluginPagePage.vue) 以 /api/plug/<插件标识>/<endpoint> 转发,
        <插件标识> 来自插件元数据 name (StarMetadata.name), 而非文件夹名。
        这里优先从星型注册表取元数据 name, 失败则解析 metadata.yaml,
        最后始终带上文件夹名以兼容旧版本。
        """
        prefixes = []

        def _add(cand):
            if isinstance(cand, str) and cand.strip():
                cand = cand.strip().strip("/")
                if cand and cand not in prefixes:
                    prefixes.append(cand)

        try:
            stars = self.context.get_all_stars() or []
        except Exception:
            stars = []
        for star in stars:
            root_dir_name = getattr(star, "root_dir_name", None)
            module_path = str(getattr(star, "module_path", "") or "")
            if root_dir_name == PLUGIN_DIR_NAME or module_path.split(".")[-1] == PLUGIN_DIR_NAME:
                _add(getattr(star, "name", None))
                break
        if not prefixes:
            _add(self._read_metadata_name())
        _add(PLUGIN_DIR_NAME)
        return prefixes

    @staticmethod
    def _read_metadata_name():
        # 轻量解析 metadata.yaml 的 name 字段, 不引入额外依赖
        try:
            meta = Path(__file__).resolve().parent / "metadata.yaml"
            if meta.exists():
                for line in meta.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("name:") and len(line) > 5:
                        name = line[5:].strip().strip("'\"")
                        if name:
                            return name
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------
    # 数据持久化
    # ------------------------------------------------------------------
    def _safe_data_path(self):
        """校验配置文件路径确在插件专属数据目录内, 防御路径穿越。"""
        target = Path(self.data_file).resolve()
        base = Path(self.data_dir).resolve()
        if base not in target.parents or target.name != "config.json":
            return None
        return target

    def _load_data(self) -> dict:
        try:
            target = self._safe_data_path()
            if target and target.exists():
                with target.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                # 与默认值合并, 防止缺字段
                g = data.setdefault("global", {})
                for k, v in DEFAULT_DATA["global"].items():
                    g.setdefault(k, v)
                data.setdefault("groups", {})
                return data
        except Exception as e:  # noqa
            logger.error(f"[{PLUGIN_NAME}] 加载配置失败: {e}")
        return json.loads(json.dumps(DEFAULT_DATA))

    def _save_data(self) -> None:
        try:
            target = self._safe_data_path()
            if not target:
                logger.error(f"[{PLUGIN_NAME}] 保存路径异常, 已拒绝写入")
                return
            with target.open("w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa
            logger.error(f"[{PLUGIN_NAME}] 保存配置失败: {e}")

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @staticmethod
    def _avatar(gid: str) -> str:
        # QQ 群头像 (OneBot / NapCat)
        return f"https://p.qlogo.cn/gh/{gid}/{gid}/100"

    @staticmethod
    def _to_minutes(t: str) -> int:
        h, m = str(t).split(":")
        return int(h) * 60 + int(m)

    def _in_range(self, now: datetime, start: str, end: str) -> bool:
        s = self._to_minutes(start)
        e = self._to_minutes(end)
        cur = now.hour * 60 + now.minute
        if s <= e:
            return s <= cur < e
        # 跨午夜, 例如 22:00 - 02:00
        return cur >= s or cur < e

    def _schedule_active(self, sched: dict, now: datetime) -> bool:
        typ = sched.get("type")
        if typ == "daily":
            return self._in_range(now, sched["start"], sched["end"])
        if typ == "weekly":
            if now.weekday() not in sched.get("days", []):
                return False
            return self._in_range(now, sched["start"], sched["end"])
        if typ == "once":
            if sched.get("date") != now.strftime("%Y-%m-%d"):
                return False
            return self._in_range(now, sched["start"], sched["end"])
        return False

    def _group_active_block(self, g: dict, now: datetime) -> bool:
        if not g.get("enabled"):
            return False
        schedules = g.get("schedules", [])
        # 时间段为可选项: 未配置任何时间段时默认全天封锁,
        # 配置后仅在命中的时间段内封锁
        if not schedules:
            return True
        return any(self._schedule_active(s, now) for s in schedules)

    def _iter_platforms(self):
        """尽可能兼容不同 AstrBot 版本的 Context 结构, 遍历所有平台实例。"""
        ctx = self.context
        for attr in ("platforms", "platform_instances"):
            obj = getattr(ctx, attr, None)
            if isinstance(obj, dict):
                yield from obj.values()
            elif isinstance(obj, (list, tuple)):
                yield from obj
        pm = getattr(ctx, "platform_manager", None)
        if pm is not None:
            for attr in ("platform_insts", "instances", "platforms"):
                obj = getattr(pm, attr, None)
                if isinstance(obj, dict):
                    yield from obj.values()
                elif isinstance(obj, (list, tuple)):
                    yield from obj
        # 兜底: 直接取 aiocqhttp
        try:
            p = ctx.get_platform("aiocqhttp")
            if p:
                yield p
        except Exception:  # noqa
            pass

    async def _get_group_list(self) -> list:
        """获取机器人所在的所有群 (跨平台尝试, 失败不影响其他平台)。"""
        groups = []
        seen = set()
        try:
            for platform in self._iter_platforms():
                try:
                    raw = None
                    if hasattr(platform, "get_group_list"):
                        raw = await platform.get_group_list()
                    elif hasattr(platform, "bot") and hasattr(platform.bot, "get_group_list"):
                        raw = await platform.bot.get_group_list()
                    if not raw:
                        continue
                    for g in raw:
                        gid = str(g.get("group_id"))
                        if not gid or gid in seen:
                            continue
                        seen.add(gid)
                        name = g.get("group_name") or gid
                        groups.append({
                            "group_id": gid,
                            "group_name": name,
                            "avatar": self._avatar(gid),
                        })
                except Exception as ex:  # noqa
                    logger.warning(f"[{PLUGIN_NAME}] 平台获取群列表失败: {ex}")
        except Exception as e:  # noqa
            logger.error(f"[{PLUGIN_NAME}] 获取群列表异常: {e}")
        return groups

    # ------------------------------------------------------------------
    # 核心拦截逻辑
    # ------------------------------------------------------------------
    # priority 取 sys.maxsize-1 (与官方 restrict_syscmd 插件一致):
    # StarHandlerRegistry 按 priority 降序执行, 值越大越先运行,
    # 确保本插件在所有其他插件/LLM 流程之前完成封锁判定。
    @filter.event_message_type(EventMessageType.ALL, priority=sys.maxsize - 1)
    async def on_message(self, event: AstrMessageEvent):
        gcfg = self.data.get("global", {})
        if not gcfg.get("enabled", True):
            self._dbg("放行: 插件总开关已关闭")
            return

        gid = event.get_group_id()
        if not gid:
            return  # 私聊不处理, 不打日志避免刷屏

        g = self.data.get("groups", {}).get(gid)
        if not g:
            ids = "、".join(self.data.get("groups", {}).keys()) or "(空)"
            self._dbg(
                f"放行: 群 {gid} 未纳入封锁控制 (当前受控群: {ids})。"
                "请到面板「行为设置」页点击「＋ 添加群聊」添加该群。"
            )
            return
        if not g.get("enabled"):
            self._dbg(f"放行: 群 {gid} 封锁已停用")
            return

        now = datetime.now()
        if not self._group_active_block(g, now):
            self._dbg(f"放行: 群 {gid} 当前不在封锁时段")
            return

        # 管理员豁免 (默认开启; 机器人主人/管理员消息不受封锁影响)
        try:
            is_admin = bool(event.is_admin())
        except Exception:
            is_admin = False
        if gcfg.get("bypass_admins", True) and is_admin:
            self._dbg(
                f"放行: 群 {gid} 发送者 {event.get_sender_id()} 是管理员, 被豁免"
                " (如需对管理员也生效, 请在「系统」页关闭管理员豁免)"
            )
            return

        # 缺口功能: 系统指令豁免 (以 / 开头的指令仍可执行, 便于管理)
        text = (event.message_str or "").strip()
        if gcfg.get("bypass_commands", True) and text.startswith("/"):
            self._dbg(f"放行: 群 {gid} 指令消息豁免 ({text[:24]})")
            return

        mode = g.get("mode", MODE_BLOCK_ALL)

        # 模式2: 被@回复 —— 被@或唤醒词时放行
        try:
            at_or_wake = bool(event.is_at_or_wake_command)
        except Exception:
            at_or_wake = False
        if mode == MODE_REPLY_AT and at_or_wake:
            self._dbg(f"放行: 群 {gid} 被@回复模式下放行 @ 消息")
            return

        # 需要封锁
        # 缺口功能: 被封锁时的提示
        notice = gcfg.get("notice_mode", NOTICE_NONE)
        try:
            if notice == NOTICE_AT:
                await event.send(event.chain_result([
                    At(qq=int(event.get_sender_id())),
                    Plain(" " + gcfg.get("notice_text", "")),
                ]))
            elif notice == NOTICE_GROUP:
                await event.send(event.plain_result(gcfg.get("notice_text", "")))
        except Exception as e:  # noqa
            logger.warning(f"[{PLUGIN_NAME}] 发送封锁提示失败: {e}")

        event.stop_event()
        logger.info(
            f"[{PLUGIN_NAME}] 已封锁群 {gid} 的消息 "
            f"(mode={mode}, sender={event.get_sender_id()})"
        )

    @filter.command("封锁状态", alias={"block_status"})
    async def block_status(self, event: AstrMessageEvent):
        """查询当前群的封锁控制状态 (指令本身不受封锁影响)"""
        gid = event.get_group_id()
        if not gid:
            yield event.plain_result("请在群聊中使用本指令。")
            return
        g = self.data.get("groups", {}).get(gid)
        if not g:
            yield event.plain_result(
                "本群未纳入封锁控制。\n请到 WebUI 面板「行为设置」页点击「＋ 添加群聊」。"
            )
            return
        now = datetime.now()
        schedules = g.get("schedules", [])
        desc = "、".join(self._schedule_desc(s) for s in schedules) if schedules \
            else "未配置 (默认全天封锁)"
        active = self._group_active_block(g, now)
        lines = [
            f"封锁控制: {'已启用' if g.get('enabled') else '已停用'}",
            f"模式: {self._mode_label(g.get('mode'))}",
            f"时间段: {desc}",
            f"当前状态: {'封锁生效中' if active else '未处于封锁时段'}",
        ]
        if g.get("enabled") and active:
            gcfg = self.data.get("global", {})
            hints = []
            if gcfg.get("bypass_admins", True):
                hints.append("管理员消息豁免 (测试请用非管理员账号)")
            if gcfg.get("bypass_commands", True):
                hints.append("/ 指令豁免")
            if g.get("mode", MODE_BLOCK_ALL) == MODE_REPLY_AT:
                hints.append("被@消息放行")
            if hints:
                lines.append("放行规则: " + "、".join(hints))
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------
    # 展示/日志辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _mode_label(mode) -> str:
        return {MODE_BLOCK_ALL: "完全封锁", MODE_REPLY_AT: "被@回复"}.get(mode, "完全封锁")

    def _schedule_desc(self, s: dict) -> str:
        typ = s.get("type")
        if typ == "daily":
            return f"每天 {s.get('start')}-{s.get('end')}"
        if typ == "weekly":
            names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            days = "、".join(names[d] for d in s.get("days", []) if isinstance(d, int) and 0 <= d < 7)
            return f"{days} {s.get('start')}-{s.get('end')}"
        if typ == "once":
            return f"{s.get('date')} {s.get('start')}-{s.get('end')}"
        return f"{s.get('start')}-{s.get('end')}"

    def _dbg(self, msg: str) -> None:
        """放行原因诊断日志: 系统「日志级别」设为 DEBUG 时输出到控制台。"""
        try:
            if str(self.data.get("global", {}).get("log_level", "INFO")).upper() == "DEBUG":
                logger.info(f"[{PLUGIN_NAME}][DEBUG] {msg}")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Web API 实现
    # ------------------------------------------------------------------
    async def api_overview(self):
        groups = await self._get_group_list()
        controlled = self.data.get("groups", {})
        now = datetime.now()
        result = []
        for g in groups:
            gid = g["group_id"]
            c = controlled.get(gid)
            result.append({
                "group_id": gid,
                "group_name": g["group_name"],
                "avatar": g["avatar"],
                "controlled": c is not None,
                "enabled": bool(c and c.get("enabled", True)),
                "mode": c.get("mode") if c else None,
                "active_now": bool(
                    c and c.get("enabled") and self._group_active_block(c, now)
                ),
            })
        return json_response({
            "groups": result,
            "global_enabled": self.data.get("global", {}).get("enabled", True),
        })

    async def api_groups(self):
        q = (request.query.get("q", "") or "").strip().lower()
        all_groups = await self._get_group_list()
        controlled = self.data.get("groups", {})
        pending = [g for g in all_groups if g["group_id"] not in controlled]
        if q:
            pending = [
                g for g in pending
                if q in g["group_name"].lower() or q in g["group_id"].lower()
            ]
        return json_response({"groups": pending})

    async def api_controlled(self):
        q = (request.query.get("q", "") or "").strip().lower()
        controlled = self.data.get("groups", {})
        now = datetime.now()
        items = []
        for gid, c in controlled.items():
            name = c.get("group_name", gid)
            if q and q not in name.lower() and q not in gid.lower():
                continue
            items.append({
                "group_id": gid,
                "group_name": name,
                "avatar": self._avatar(gid),
                "enabled": c.get("enabled", True),
                "mode": c.get("mode", MODE_BLOCK_ALL),
                "schedules": c.get("schedules", []),
                "active_now": self._group_active_block(c, now),
            })
        return json_response({"groups": items})

    async def api_group_add(self):
        payload = await request.json(default={})
        gid = str(payload.get("group_id", "")).strip()
        name = payload.get("group_name") or gid
        if not gid:
            return error_response("缺少 group_id")
        groups = self.data.setdefault("groups", {})
        if gid in groups:
            return error_response("该群已在控制列表中")
        groups[gid] = {
            "group_id": gid,
            "group_name": name,
            "enabled": True,
            "mode": payload.get("mode", MODE_BLOCK_ALL),
            "schedules": [],
        }
        self._save_data()
        return json_response({"ok": True})

    async def api_group_remove(self):
        payload = await request.json(default={})
        gid = str(payload.get("group_id", "")).strip()
        groups = self.data.get("groups", {})
        if gid in groups:
            del groups[gid]
            self._save_data()
            return json_response({"ok": True})
        return error_response("群不存在")

    async def api_group_toggle(self):
        payload = await request.json(default={})
        gid = str(payload.get("group_id", "")).strip()
        enabled = bool(payload.get("enabled"))
        groups = self.data.get("groups", {})
        if gid not in groups:
            # 总览开关打开一个未受控群 -> 自动加入
            groups[gid] = {
                "group_id": gid,
                "group_name": payload.get("group_name") or gid,
                "enabled": enabled,
                "mode": payload.get("mode", MODE_BLOCK_ALL),
                "schedules": [],
            }
            self._save_data()
            return json_response({"ok": True, "added": True})
        groups[gid]["enabled"] = enabled
        self._save_data()
        return json_response({"ok": True})

    async def api_group_mode(self):
        payload = await request.json(default={})
        gid = str(payload.get("group_id", "")).strip()
        mode = payload.get("mode")
        if mode not in (MODE_BLOCK_ALL, MODE_REPLY_AT):
            return error_response("非法模式")
        g = self.data.get("groups", {}).get(gid)
        if not g:
            return error_response("群不存在")
        g["mode"] = mode
        self._save_data()
        return json_response({"ok": True})

    async def api_schedule_add(self):
        payload = await request.json(default={})
        gid = str(payload.get("group_id", "")).strip()
        g = self.data.get("groups", {}).get(gid)
        if not g:
            return error_response("群不存在")
        sched = payload.get("schedule", {})
        typ = sched.get("type")
        if typ not in ("daily", "weekly", "once"):
            return error_response("非法时间段类型")
        try:
            self._to_minutes(sched["start"])
            self._to_minutes(sched["end"])
        except Exception:  # noqa
            return error_response("时间格式错误, 应为 HH:MM")
        if typ == "weekly" and not sched.get("days"):
            return error_response("请至少选择一个星期")
        if typ == "once" and not sched.get("date"):
            return error_response("请选择生效日期")
        entry = {
            "id": uuid.uuid4().hex[:8],
            "type": typ,
            "start": sched["start"],
            "end": sched["end"],
        }
        if typ == "weekly":
            entry["days"] = sched.get("days", [])
        if typ == "once":
            entry["date"] = sched["date"]
        g.setdefault("schedules", []).append(entry)
        self._save_data()
        return json_response({"ok": True, "schedule": entry})

    async def api_schedule_remove(self):
        payload = await request.json(default={})
        gid = str(payload.get("group_id", "")).strip()
        sid = payload.get("schedule_id")
        g = self.data.get("groups", {}).get(gid)
        if not g:
            return error_response("群不存在")
        g["schedules"] = [s for s in g.get("schedules", []) if s.get("id") != sid]
        self._save_data()
        return json_response({"ok": True})

    async def api_settings(self):
        if request.method == "GET":
            return json_response(self.data.get("global", {}))
        payload = await request.json(default={})
        g = self.data.setdefault("global", {})
        for k in ("enabled", "bypass_admins", "bypass_commands"):
            if k in payload:
                g[k] = bool(payload[k])
        if "log_level" in payload:
            g["log_level"] = payload["log_level"]
        if "notice_mode" in payload and payload["notice_mode"] in (
            NOTICE_NONE, NOTICE_AT, NOTICE_GROUP
        ):
            g["notice_mode"] = payload["notice_mode"]
        if "notice_text" in payload:
            g["notice_text"] = payload["notice_text"]
        self._save_data()
        return json_response({"ok": True, "global": g})

    async def api_reset(self):
        self.data = json.loads(json.dumps(DEFAULT_DATA))
        self._save_data()
        return json_response({"ok": True})

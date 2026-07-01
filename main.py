import re
import random
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import At, Plain

@register("fast_car_lottery", "YourName", "快速车统计抽取插件(全功能带帮助版)", "1.8.4")
class FastCarLotteryPlugin(Star):
    # 管理员配置提示（精简版）
    ADMIN_CONFIG_HINT = (
        "\n\n💡 管理员指令需先在 AstrBot 后台 → 配置 → 普通配置 → 平台配置 → 管理员ID 中添加 QQ号 或 用户ID。"
    )

    def __init__(self, context: Context):
        super().__init__(context)
        self.stats_data = {}

    # ====================== 新版 @机器人 判断 (v3.4.28+ 专用) ======================
    def is_at_me(self, event: AstrMessageEvent) -> bool:
        bot_id = event.message_obj.self_id
        for seg in event.message_obj.message:
            if isinstance(seg, At) and str(seg.qq) == str(bot_id):
                return True
        return False

    # ====================== 通用解析：提取队列数字 + 所有@的用户（排除开头触发用的@机器人） ======================
    def _parse_queue_and_members(self, event: AstrMessageEvent):
        """
        解析消息链：提取【单个队列数字】、【所有被@的用户（含ID和昵称）】
        - 排除开头触发指令的@机器人
        - 允许用户在指令中额外@机器人进行操作
        返回: (队列号, [{"id": "xxx", "name": "xxx"}], 错误信息)
        """
        msg_segments = event.message_obj.message
        bot_id = event.message_obj.self_id
        queue_num = None
        members = []
        # 标记是否已经跳过了开头的@机器人
        skipped_initial_at_me = False

        # 遍历消息链，分离文本数字 和 @组件
        for seg in msg_segments:
            if isinstance(seg, Plain):
                # 从纯文本中提取数字（队列号）
                text_nums = re.findall(r"\d+", seg.text)
                if text_nums and queue_num is None:
                    # 只取第一个数字作为队列（限制：一次仅一个队列）
                    queue_num = text_nums[0]
            elif isinstance(seg, At):
                # 开头第一个@如果是机器人自己，则跳过
                if not skipped_initial_at_me and str(seg.qq) == str(bot_id):
                    skipped_initial_at_me = True
                    continue
                # 其他@组件（包括后续再次@机器人）都正常加入列表
                members.append({
                    "id": str(seg.qq),
                    "name": seg.name if hasattr(seg, 'name') else f"QQ:{seg.qq}"
                })

        # 校验参数合法性
        if queue_num is None:
            return None, [], "⚠️ 未填写队列数字，请格式：/快速车添加/删除 数字 @群友"
        if not members:
            return None, [], "⚠️ 未@任何群友，请@需要操作的群友"

        return queue_num, members, ""

    async def initialize(self):
        self.stats_data = await self.get_kv_data("fast_car_data", {})
        migrated_count = 0
        for session_id, data in list(self.stats_data.items()):
            if isinstance(data, dict) and "current" not in data and "last" not in data:
                self.stats_data[session_id] = {
                    "current": data,
                    "last": None
                }
                migrated_count += 1
        if migrated_count > 0:
            await self.save_data()
            logger.info(f"快速车插件：成功平滑迁移了 {migrated_count} 条旧版数据结构。")
        logger.info(f"快速车插件已加载，当前信任的会话数：{len(self.stats_data)}")

    async def save_data(self):
        await self.put_kv_data("fast_car_data", self.stats_data)

    def _get_session_data(self, event: AstrMessageEvent) -> dict:
        group_id = event.message_obj.group_id
        session_id = f"group_{group_id}" if group_id else f"private_{event.get_sender_id()}"
        if session_id not in self.stats_data:
            self.stats_data[session_id] = {"current": None, "last": None}
        return self.stats_data[session_id]

    @filter.command("快速车")
    async def fast_car_simple_menu(self, event: AstrMessageEvent):
        if not self.is_at_me(event): return
        session_data = self._get_session_data(event)
        current = session_data.get("current")
        active_prompt = ""
        if current and current.get("active"):
            targets_str = "、".join(current["targets"])
            active_prompt = f"\n\n🟢 【当前正在统计的队列】：{targets_str}\n👉 提示：请直接在群内发送对应数字加入队列。"
        simple_help = ("🚗💨 快速车简易指南 💨🚗\n💡【群友上车规则】\n群友在群里直接发送对应的目标数字即可完成登记。如果中途改变主意发送了其他数字，系统会自动将其从旧队列移除并登记到新队列。\n\n⚠️ 其余指令仅bot管理员可操作\n1️⃣ /快速车统计 [数字1] [数字2] ...\n🔍 例如：/快速车统计 9 10 11\n\n📖 更多帮助请发送：/快速车帮助") + active_prompt
        yield event.plain_result(simple_help)

    @filter.command("快速车帮助")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def fast_car_help(self, event: AstrMessageEvent):
        if not self.is_at_me(event): return
        help_text = """💡【群友上车规则】
群友在群里直接发送对应的目标数字即可完成登记。如果中途改变主意发送了其他数字，系统会自动将其从旧队列移除并登记到新队列（自动换乘改签）。

📌【核心管理指令】
1️⃣ /快速车统计 [数字1] [数字2] ...
    👉 开启新一轮统计。支持1-5个数字，空格分隔。
    🔍 例如：/快速车统计 9 10 11
2️⃣ /添加队列 [数字]
3️⃣ /修改队列 [旧数字] [新数字]
    👉 变更已有队列数字，队列中的群友及中签记录会平滑迁移。
    🔍 例如：/修改队列 10 11
4️⃣ /结束统计
5️⃣ /快速抽取[数量]
    👉 从各个队列里随机抽取指定人数并展示（支持不加空格，如 /快速抽取2）。
6️⃣ /快速车提醒[数字] (别称: /快速提醒[数字])
    👉 强艾特(@)指定队列里【被抽中】的群友。

🆕【批量操作队列（单次仅支持一个队列）】
7️⃣ /快速车添加 [队列数字] @群友1 @群友2...
    👉 批量将@的群友/机器人加入指定队列
    🔍 示例：/快速车添加 9 @张三 @机器人
8️⃣ /快速车删除 [队列数字] @群友1 @群友2...
    👉 批量将@的群友/机器人移出指定队列
    🔍 示例：/快速车删除 9 @张三 @机器人

📊【数据查看与归档】
9️⃣ /统计详细记录 (别称: /快速车详细, /统计详细, /快速车记录)
    👉 查看活跃名单及中签抽取记录。
🔟 /上次统计详细 : 查看上一轮历史名单及历史抽取记录。
1️⃣1️⃣ /清空统计 : 擦除当前活跃队列，并在擦除前自动备份到历史区。
1️⃣2️⃣ /快速车帮助 : 显示本帮助菜单。"""
        yield event.plain_result(help_text + self.ADMIN_CONFIG_HINT)

    # ====================== 快速车添加 指令 ======================
    @filter.command("快速车添加")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def add_member_to_queue(self, event: AstrMessageEvent):
        if not self.is_at_me(event): return
        session_data = self._get_session_data(event)
        current = session_data["current"]

        # 前置校验：必须有活跃统计
        if not current or not current["active"]:
            yield event.plain_result("⚠️ 当前无正在进行的统计，无法添加成员！")
            return

        # 解析队列号 和 被@的用户（带昵称）
        queue_num, members, err_msg = self._parse_queue_and_members(event)
        if err_msg:
            yield event.plain_result(err_msg)
            return

        # 校验队列是否存在
        if queue_num not in current["targets"]:
            yield event.plain_result(f"⚠️ 队列 [{queue_num}] 不存在！")
            return

        queue_list = current["queues"][queue_num]
        success_list = []    # 成功添加的用户昵称
        exist_list = []      # 已在队列中的用户昵称

        # 遍历所有@的用户，执行添加
        for member in members:
            uid = member["id"]
            name = member["name"]
            
            # 查找队列内是否已有该用户
            exist_user = next((u for u in queue_list if u["id"] == uid), None)
            if exist_user:
                exist_list.append(exist_user["name"])
            else:
                queue_list.append({"id": uid, "name": name})
                success_list.append(name)

        await self.save_data()

        # 拼接返回文案
        res_lines = [f"✅ 队列 [{queue_num}] 批量添加结果："]
        if success_list:
            res_lines.append(f"成功加入：{ '、'.join(success_list) }")
        if exist_list:
            res_lines.append(f"已在队列：{ '、'.join(exist_list) }")
        if not success_list and not exist_list:
            res_lines.append("无有效操作")

        yield event.plain_result("\n".join(res_lines))

    # ====================== 快速车删除 指令 ======================
    @filter.command("快速车删除")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def remove_member_from_queue(self, event: AstrMessageEvent):
        if not self.is_at_me(event): return
        session_data = self._get_session_data(event)
        current = session_data["current"]

        # 前置校验：必须有活跃统计
        if not current or not current["active"]:
            yield event.plain_result("⚠️ 当前无正在进行的统计，无法移除成员！")
            return

        # 解析队列号 和 被@的用户（带昵称）
        queue_num, members, err_msg = self._parse_queue_and_members(event)
        if err_msg:
            yield event.plain_result(err_msg)
            return

        # 校验队列是否存在
        if queue_num not in current["targets"]:
            yield event.plain_result(f"⚠️ 队列 [{queue_num}] 不存在！")
            return

        queue_list = current["queues"][queue_num]
        success_list = []    # 成功移除的用户昵称
        not_exist_list = []  # 不在队列中的用户昵称

        # 遍历所有@的用户，执行删除
        for member in members:
            uid = member["id"]
            name = member["name"]
            
            exist_user = next((u for u in queue_list if u["id"] == uid), None)
            if exist_user:
                queue_list.remove(exist_user)
                success_list.append(exist_user["name"])
            else:
                not_exist_list.append(name)

        await self.save_data()

        # 拼接返回文案
        res_lines = [f"✅ 队列 [{queue_num}] 批量移除结果："]
        if success_list:
            res_lines.append(f"成功移出：{ '、'.join(success_list) }")
        if not_exist_list:
            res_lines.append(f"不在队列：{ '、'.join(not_exist_list) }")
        if not success_list and not_exist_list:
            res_lines.append("无有效操作")

        yield event.plain_result("\n".join(res_lines))

    # ========== 以下为原有所有指令，无修改 ==========
    @filter.command("快速车统计")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def start_stat(self, event: AstrMessageEvent):
        if not self.is_at_me(event): return
        clean_text = re.sub(r'@\S+', '', event.message_str)
        text = clean_text.replace("/快速车统计", "").replace("快速车统计", "").strip()
        numbers = re.findall(r'\d+', text)
        if not numbers or len(numbers) > 5:
            yield event.plain_result("⚠️ 参数错误：请在指令后输入 1 到 5 个数字（使用空格分隔）。")
            return
        session_data = self._get_session_data(event)
        if session_data["current"]:
            session_data["last"] = session_data["current"]
        session_data["current"] = {"active": True, "targets": numbers, "queues": {num: [] for num in numbers}, "draw_results": {}}
        await self.save_data()
        targets_str = "、".join(numbers)
        yield event.plain_result(f"✅ 开始新一轮统计！\n正在监听目标队列：{targets_str}。\n👉 请直接在群内发送对应数字加入队列。")

    @filter.command("添加队列")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def add_queue(self, event: AstrMessageEvent):
        if not self.is_at_me(event): return
        session_data = self._get_session_data(event)
        current = session_data["current"]
        if not current or not current["active"]:
            yield event.plain_result("⚠️ 当前没有正在进行的活跃统计，无法添加新队列。")
            return
        clean_text = re.sub(r'@\S+', '', event.message_str)
        text = clean_text.replace("/添加队列", "").replace("添加队列", "").strip()
        numbers = re.findall(r'\d+', text)
        if not numbers:
            yield event.plain_result("⚠️ 请输入要添加的队列数字。")
            return
        num = numbers[0]
        if num in current["targets"]:
            yield event.plain_result(f"⚠️ 队列 [{num}] 已经存在。")
            return
        current["targets"].append(num)
        current["queues"][num] = []
        await self.save_data()
        yield event.plain_result(f"✅ 成功追加新队列 [{num}]！")

    @filter.command("修改队列")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def modify_queue(self, event: AstrMessageEvent):
        if not self.is_at_me(event): return
        session_data = self._get_session_data(event)
        current = session_data["current"]
        if not current: return
        clean_text = re.sub(r'@\S+', '', event.message_str)
        text = clean_text.replace("/修改队列", "").replace("修改队列", "").strip()
        numbers = re.findall(r'\d+', text)
        if len(numbers) < 2:
            yield event.plain_result("⚠️ 参数错误，格式：/修改队列 [原数字] [新数字]")
            return
        old_num, new_num = numbers[0], numbers[1]
        if old_num not in current["targets"]: return
        idx = current["targets"].index(old_num)
        current["targets"][idx] = new_num
        current["queues"][new_num] = current["queues"].pop(old_num)
        if "draw_results" in current and old_num in current["draw_results"]:
            current["draw_results"][new_num] = current["draw_results"].pop(old_num)
        await self.save_data()
        yield event.plain_result(f"✅ 成功将队列 [{old_num}] 变更为 [{new_num}]！")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def on_normal_message(self, event: AstrMessageEvent):
        session_data = self._get_session_data(event)
        current = session_data["current"]
        if current and current["active"]:
            text = event.message_str.strip()
            clean_text = re.sub(r'@\S+', '', text).strip()
            normalized_text = re.sub(r'[、，,/;；+\s]+', ' ', clean_text).strip()
            tokens = normalized_text.split()
            if tokens and all(t in current["targets"] for t in tokens):
                event.stop_event()
                user_id = event.get_sender_id()
                user_name = event.get_sender_name()
                valid_numbers = list(dict.fromkeys(tokens))
                was_in_queue = False
                for target, queue_list in current["queues"].items():
                    if any(user["id"] == user_id for user in queue_list):
                        was_in_queue = True
                    current["queues"][target] = [user for user in queue_list if user["id"] != user_id]
                for num in valid_numbers:
                    current["queues"][num].append({"id": user_id, "name": user_name})
                await self.save_data()
                
                targets_str = "、".join(current["targets"])
                hint_suffix = f"\n👉 提示：请直接在群内发送对应数字加入队列，目前队列：{targets_str}"
                action_str = "成功改签到" if was_in_queue else "成功记录到"
                
                if len(valid_numbers) == 1:
                    num = valid_numbers[0]
                    yield event.plain_result(f"📌 [{user_name}] 已{action_str} [{num}] 队列！(当前人数：{len(current['queues'][num])}){hint_suffix}")
                else:
                    queues_str = "、".join([f"[{n}]" for n in valid_numbers])
                    yield event.plain_result(f"📌 [{user_name}] 已{action_str} {queues_str} 队列！{hint_suffix}")

    @filter.command("结束统计")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def end_stat(self, event: AstrMessageEvent):
        if not self.is_at_me(event): return
        session_data = self._get_session_data(event)
        current = session_data["current"]
        if current and current["active"]:
            current["active"] = False
            await self.save_data()
            result_lines = ["🛑 统计功能已关闭，使用 /快速抽取[抽取数量]进行抽取。\n", "📊 快速车统计详细名单："]
            for t in current["targets"]:
                q = current["queues"][t]
                names_str = ", ".join([user["name"] for user in q]) if q else "无人排队"
                result_lines.append(f"▶ [{t}] 队列 ({len(q)}人): {names_str}")
            yield event.plain_result("\n".join(result_lines))
        else:
            yield event.plain_result("⚠️ 当前没有任何正在进行的统计活动。")

    @filter.regex(r"快速抽取\s*\d*")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def draw_stat(self, event: AstrMessageEvent):
        if not self.is_at_me(event): return
        session_data = self._get_session_data(event)
        current = session_data["current"]
        if not current: return
        if current.get("active", False):
            yield event.plain_result("⚠️ 统计尚未结束！请先发送 /结束统计 锁定队列关闭上车通道后，再进行抽取。")
            return
        match = re.search(r"快速抽取\s*(\d+)", event.message_str)
        draw_count = int(match.group(1)) if match else 1
        draw_count = max(1, draw_count)
        targets = current["targets"]
        queues = current["queues"]
        current["draw_results"] = {}
        result_lines = [f"🎉 抽取结果 (每个队列抽取 {draw_count} 人)："]
        for t in targets:
            q = queues[t]
            if not q:
                result_lines.append(f"▶ [{t}] 队列: 无人参与")
                current["draw_results"][t] = []
                continue
            actual_draw_count = min(draw_count, len(q))
            winners = random.sample(q, actual_draw_count)
            current["draw_results"][t] = winners
            winners_str = "、".join([f"{winner['name']}({winner['id']})" for winner in winners])
            result_lines.append(f"▶ [{t}] 队列 ({len(q)}人): {winners_str}")
        result_lines.append("\n💡 管理员可@全员通知，或者/快速车提醒[队列数字]")
        await self.save_data()
        yield event.plain_result("\n".join(result_lines))

    @filter.regex(r"(快速车提醒|快速提醒)\s*\d*")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def remind_stat(self, event: AstrMessageEvent):
        if not self.is_at_me(event): return
        session_data = self._get_session_data(event)
        current = session_data["current"]
        if not current: return
        match = re.search(r"(快速车提醒|快速提醒)\s*(\d+)", event.message_str)
        if not match: return
        target_num = match.group(2)
        winners_list = current.get("draw_results", {}).get(target_num, [])
        if not winners_list:
            yield event.plain_result("⚠️ 请先进行抽取。")
            return
        chain = [Plain(f"📢 快速车发车提醒！[{target_num}] 队列的中签群友请注意：\n\n")]
        for user in winners_list:
            chain.append(At(qq=user["id"]))
            chain.append(Plain(f" ({user['id']}) "))
        chain.append(Plain("\n\n🚗 车已备好，请速速到场！"))
        yield event.chain_result(chain)
        
    @filter.command("清空统计")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def clear_stat(self, event: AstrMessageEvent):
        if not self.is_at_me(event): return
        session_data = self._get_session_data(event)
        if session_data["current"]:
            session_data["last"] = session_data["current"]
            session_data["current"] = None
            await self.save_data()
            yield event.plain_result("✅ 已成功清空当前群组的活跃统计。")

    @filter.command("统计详细记录", alias={"快速车详细", "统计详细", "快速车记录"})
    async def show_detail_stat(self, event: AstrMessageEvent):
        if not self.is_at_me(event): return
        session_data = self._get_session_data(event)
        current = session_data["current"]
        if not current:
            yield event.plain_result("⚠️ 当前没有任何活跃统计数据。")
            return
        status_str = "🟢 正在收集数据中..." if current["active"] else "🔴 统计已结束"
        result_lines = [f"📊 快速车统计详细名单 ({status_str})："]
        for t in current["targets"]:
            q = current["queues"][t]
            names_str = ", ".join([user["name"] for user in q]) if q else "无人排队"
            result_lines.append(f"▶ [{t}] 队列 ({len(q)}人): {names_str}")
        result_lines.append("\n🎁 抽取结果记录：")
        if not current.get("draw_results"):
            result_lines.append("👉 先 /结束统计 再使用 /快速抽取 进行选人。")
        else:
            for t in current["targets"]:
                winners = current["draw_results"].get(t, [])
                winners_str = ", ".join([f"{w['name']}({w['id']})" for w in winners]) if winners else "无中签"
                result_lines.append(f"▶ [{t}] 队列中签: {winners_str}")
        yield event.plain_result("\n".join(result_lines))

    @filter.command("上次统计详细")
    async def show_last_detail_stat(self, event: AstrMessageEvent):
        if not self.is_at_me(event): return
        session_data = self._get_session_data(event)
        last = session_data["last"]
        if not last:
            yield event.plain_result("⚠️ 本群暂无上一次统计的历史归档记录。")
            return
        result_lines = ["📜 上次快速车历史详细名单归档："]
        for t in last["targets"]:
            q = last["queues"][t]
            names_str = ", ".join([user["name"] for user in q]) if q else "无人排队"
            result_lines.append(f"▶ [{t}] 队列 ({len(q)}人): {names_str}")
        result_lines.append("\n🎁 历史抽取状态：")
        last_draw_results = last.get("draw_results", {})
        for t in last["targets"]:
            winners = last_draw_results.get(t, [])
            winners_str = ", ".join([f"{w['name']}({w['id']})" for w in winners]) if winners else "无中签记录"
            result_lines.append(f"▶ [{t}] 队列中签: {winners_str}")
        yield event.plain_result("\n".join(result_lines))

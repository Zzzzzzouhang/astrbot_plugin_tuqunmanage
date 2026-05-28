import re
import random
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import At, Plain

@register("fast_car_lottery", "YourName", "快速车统计抽取插件(全功能带帮助版)", "1.5.0")
class FastCarLotteryPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.stats_data = {}

    async def initialize(self):
        """插件初始化：从 KV 存储恢复数据并兼容旧结构"""
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
        """持久化保存数据到 KV 存储"""
        await self.put_kv_data("fast_car_data", self.stats_data)

    def _get_session_data(self, event: AstrMessageEvent) -> dict:
        """获取或初始化当前会话的双层数据结构"""
        group_id = getattr(event.message_obj, "group_id", None)
        session_id = f"group_{group_id}" if group_id else f"private_{event.get_sender_id()}"
        
        if session_id not in self.stats_data:
            self.stats_data[session_id] = {
                "current": None,
                "last": None
            }
        return self.stats_data[session_id]

    # ================= 新增功能 0: 快速车帮助 =================
    @filter.command("快速车帮助")
    async def fast_car_help(self, event: AstrMessageEvent):
        """显示快速车插件的所有指令和使用指南"""
        help_text = (
            "🚗💨 **快速车统计抽取插件指南** 💨🚗\n\n"
            "📌 **核心管理指令：**\n"
            "1️⃣ `/快速车统计 [数字1] [数字2] ...` \n"
            "   👉 开启新一轮统计。支持1-5个数字，空格分隔。\n"
            "   *🔍 例如：/快速车统计 9 10 11*\n"
            "2️⃣ `/结束统计` \n"
            "   👉 锁定当前队伍，关闭数字监听，不再接收新登记。\n"
            "3️⃣ `/快速抽取 [数量]` \n"
            "   👉 从各个队列里随机抽取指定人数并 @ 提醒。\n"
            "   *🔍 例如：/快速抽取 2 (不填默认抽取1人)*\n\n"
            "📊 **数据查看与归档：**\n"
            "4️⃣ `/统计详细记录` : 查看当前所有队列的活跃排队名单。\n"
            "5️⃣ `/上次统计详细` : 查看上一轮（被覆盖或清空）的历史名单。\n"
            "6️⃣ `/清空统计` : 擦除当前活跃队列，并在擦除前自动备份到历史区。\n"
            "7️⃣ `/快速车帮助` : 显示本帮助菜单。\n\n"
            "💡 **群友上车规则：**\n"
            "群友在群里**直接发送对应的目标数字**即可完成登记。如果中途改变主意，发了其他目标数字，系统会自动将其从旧队列移除并登记到新队列（**静默换乘改签**），不刷屏打扰群聊。"
        )
        yield event.plain_result(help_text)

    # ================= 功能 1: 开启统计 =================
    @filter.command("快速车统计")
    async def start_stat(self, event: AstrMessageEvent):
        """开启快速车统计，后面需跟 1-5 个数字。例如：/快速车统计 9 10 11"""
        text = event.message_str.replace("/快速车统计", "").strip()
        numbers = re.findall(r'\d+', text)

        if not numbers or len(numbers) > 5:
            yield event.plain_result("⚠️ 参数错误：请在指令后输入 1 到 5 个数字（使用空格分隔）。\n例如：/快速车统计 9 10 11")
            return

        session_data = self._get_session_data(event)
        
        if session_data["current"]:
            session_data["last"] = session_data["current"]
        
        session_data["current"] = {
            "active": True,
            "targets": numbers,
            "queues": {num: [] for num in numbers}
        }
        
        await self.save_data()
        
        targets_str = "、".join(numbers)
        yield event.plain_result(f"✅ 开始新一轮统计！\n正在监听目标队列：{targets_str}。\n请直接在群内发送对应数字加入队列。")

# ================= 功能 2: 持续记录用户 (支持自动改签与静默模式) =================
    # 🌟 修复点 1：增加 priority=1，确保全量监听在 LLM 之前触发
    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def on_normal_message(self, event: AstrMessageEvent):
        """全局消息监听。新用户发送则回复，老用户发送数字则自动切换队列（移除旧的）且不回复"""
        session_data = self._get_session_data(event)
        current = session_data["current"]
        
        if current and current["active"]:
            text = event.message_str.strip()
            
            # 检查发送的内容是否在当前监听的数字列表中
            if text in current["targets"]:
                # 🌟 修复点 2：一旦匹配成功，立刻拦截事件，防止大模型误回复该数字
                event.stop_event()
                
                user_id = event.get_sender_id()
                user_name = event.get_sender_name()
                
                is_changed = False
                for queue_list in current["queues"].values():
                    for user in queue_list:
                        if user["id"] == user_id:
                            queue_list.remove(user)
                            is_changed = True
                            break
                    if is_changed:
                        break
                
                current["queues"][text].append({"id": user_id, "name": user_name})
                await self.save_data()
                
                if not is_changed:
                    yield event.plain_result(f"📌 [{user_name}] 已成功记录到 [{text}] 队列！(当前人数：{len(current['queues'][text])})")
                else:
                    # 老用户换乘改签：由于上面执行了 stop_event()，这里直接 return 就能做到完全静默，不会触发大模型
                    return
    # ================= 功能 3: 结束统计 =================
    @filter.command("结束统计")
    async def end_stat(self, event: AstrMessageEvent):
        """关闭统计功能，群友再发数字不再回应"""
        session_data = self._get_session_data(event)
        current = session_data["current"]
        
        if current and current["active"]:
            current["active"] = False
            await self.save_data()
            yield event.plain_result("🛑 统计功能已关闭。发送数字不再记录，可以使用 /快速抽取 进行抽人。")
        else:
            yield event.plain_result("⚠️ 当前没有任何正在进行的统计活动。")

    # ================= 功能 4: 快速抽取 (带 At 功能) =================
    @filter.command("快速抽取")
    async def draw_stat(self, event: AstrMessageEvent):
        """每个队列随机抽取指定数量的人并 @ 他们。例如：/快速抽取 2"""
        session_data = self._get_session_data(event)
        current = session_data["current"]
        
        if not current:
            yield event.plain_result("⚠️ 当前没有任何统计数据，请先使用 /快速车统计 指令！")
            return
            
        text = event.message_str.replace("/快速抽取", "").strip()
        counts = re.findall(r'\d+', text)
        draw_count = int(counts[0]) if counts else 1
        
        if draw_count < 1:
            draw_count = 1

        targets = current["targets"]
        queues = current["queues"]
        
        chain = [Plain(f"🎉 抽取结果 (每个队列抽取 {draw_count} 人)：\n")]
        
        for t in targets:
            q = queues[t]
            if not q:
                chain.append(Plain(f"▶ [{t}] 队列: 无人参与\n"))
                continue
                
            actual_draw_count = min(draw_count, len(q))
            winners = random.sample(q, actual_draw_count)
            
            chain.append(Plain(f"▶ [{t}] 队列 ({len(q)}人): "))
            for winner in winners:
                chain.append(At(qq=str(winner['id'])))
                chain.append(Plain(" ")) 
            chain.append(Plain("\n"))
            
        yield MessageEventResult(message_chain=chain)

    # ================= 功能 5: 清空统计 =================
    @filter.command("清空统计")
    async def clear_stat(self, event: AstrMessageEvent):
        """清空当前群组的所有统计数据，清空前会自动备份到上次统计中"""
        session_data = self._get_session_data(event)
        
        if session_data["current"]:
            session_data["last"] = session_data["current"]
            session_data["current"] = None
            await self.save_data()
            yield event.plain_result("✅ 已成功清空当前群组的活跃统计。旧队列已归档，您可以发送 /上次统计详细 查看历史。")
        else:
            yield event.plain_result("⚠️ 当前本群没有任何活跃统计数据，无需清空。")

    # ================= 功能 6: 统计详细记录 =================
    @filter.command("统计详细记录")
    async def show_detail_stat(self, event: AstrMessageEvent):
        """查看当前群各个队列详细的排队人员名单"""
        session_data = self._get_session_data(event)
        current = session_data["current"]
        
        if not current:
            yield event.plain_result("⚠️ 当前没有任何活跃统计数据，请先使用 /快速车统计 指令！")
            return
            
        status_str = "🟢 正在收集数据中..." if current["active"] else "🔴 统计已结束"
        targets = current["targets"]
        queues = current["queues"]
        
        result_lines = [f"📊 快速车统计详细名单 ({status_str})："]
        for t in targets:
            q = queues[t]
            if not q:
                result_lines.append(f"▶ [{t}] 队列 (0人): 无人排队")
            else:
                names_str = ", ".join([user["name"] for user in q])
                result_lines.append(f"▶ [{t}] 队列 ({len(q)}人): {names_str}")
                
        yield event.plain_result("\n".join(result_lines))

    # ================= 功能 7: 上次统计详细 =================
    @filter.command("上次统计详细")
    async def show_last_detail_stat(self, event: AstrMessageEvent):
        """查看上一次（已被清空或已被覆盖的）统计记录名单"""
        session_data = self._get_session_data(event)
        last = session_data["last"]
        
        if not last:
            yield event.plain_result("⚠️ 本群暂无上一次统计的历史归档记录。")
            return
            
        targets = last["targets"]
        queues = last["queues"]
        
        result_lines = [f"📜 上次快速车历史详细名单归档："]
        for t in targets:
            q = queues[t]
            if not q:
                result_lines.append(f"▶ [{t}] 队列: 历史无人排队")
            else:
                names_str = ", ".join([user["name"] for user in q])
                result_lines.append(f"▶ [{t}] 队列 ({len(q)}人): {names_str}")
                
        yield event.plain_result("\n".join(result_lines))

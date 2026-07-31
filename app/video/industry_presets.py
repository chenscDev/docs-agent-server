"""行业玩法 / 预制口播灵感目录（复用现有视觉模板与生成类型）。"""

from __future__ import annotations

from typing import Any


# 行业维度（端上 Tab / 筛选）
INDUSTRY_CATALOG: list[dict[str, Any]] = [
    {
        "id": "ecommerce",
        "name": "电商",
        "emoji": "🛍️",
        "description": "种草、促销、卖点快切",
    },
    {
        "id": "local-life",
        "name": "本地生活",
        "emoji": "🍜",
        "description": "到店、探店、团购转化",
    },
    {
        "id": "hiring",
        "name": "招聘",
        "emoji": "💼",
        "description": "岗位亮点与投递号召",
    },
    {
        "id": "lifestyle",
        "name": "生活趣味",
        "emoji": "✨",
        "description": "日常、萌宠、亲子玩梗",
    },
]


def _preset(
    *,
    id: str,
    industry: str,
    name: str,
    emoji: str,
    color: str,
    prompt: str,
    generation_type_id: str,
    template_id: str,
    bgm_track_id: str,
    planner_extra_hint: str,
    sample_beats: list[str],
    cover_seed: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "industry": industry,
        "name": name,
        "label": name,
        "emoji": emoji,
        "color": color,
        "prompt": prompt,
        "generationTypeId": generation_type_id,
        "templateId": template_id,
        "defaultBgmTrackId": bgm_track_id,
        "plannerExtraHint": planner_extra_hint,
        "sampleBeats": sample_beats,
        "coverSeed": cover_seed or id,
    }


INDUSTRY_PRESET_CATALOG: list[dict[str, Any]] = [
    # —— 电商 ——
    _preset(
        id="ecom-seed",
        industry="ecommerce",
        name="30秒种草不硬广",
        emoji="🛍️",
        color="#FFEDD5",
        prompt=(
            "30 秒种草短视频：先讲真实使用场景，再说 2～3 个痛点解决，"
            "结尾轻轻提下单，语气像闺蜜安利，别硬广堆参数。"
        ),
        generation_type_id="narration",
        template_id="talking-captions",
        bgm_track_id="soft-pink",
        planner_extra_hint=(
            "电商种草结构：痛点开场→最多3个卖点各一镜→信任点→单一行动号召；"
            "禁用绝对化用语与虚假促销。"
        ),
        sample_beats=["痛点开场", "卖点一", "卖点二", "行动号召"],
    ),
    _preset(
        id="ecom-promo",
        industry="ecommerce",
        name="限时优惠倒计时",
        emoji="🎉",
        color="#FEF2F2",
        prompt=(
            "限时促销短视频：先说优惠力度和截止时间，再列 2～3 个爆款点，"
            "结尾催促行动，语气急但不吼。"
        ),
        generation_type_id="kinetic",
        template_id="kinetic-text",
        bgm_track_id="bright-pulse",
        planner_extra_hint=(
            "促销快闪：首镜必须含优惠力度/截止时间；中间卖点极短标题；"
            "末镜单一 CTA；不要编造库存与销量。"
        ),
        sample_beats=["优惠开场", "爆款点1", "爆款点2", "催促下单"],
    ),
    _preset(
        id="ecom-unbox",
        industry="ecommerce",
        name="开箱三秒出笑点",
        emoji="📦",
        color="#FEF3C7",
        prompt=(
            "开箱口播：前三秒抓人，中间讲「拿到手最惊喜的一点」，"
            "结尾问观众要不要同款链接，口语像朋友聊天。"
        ),
        generation_type_id="narration",
        template_id="talking-captions",
        bgm_track_id="soft-pink",
        planner_extra_hint=(
            "开箱结构：钩子开场→开箱瞬间→一个惊喜点→互动 CTA；"
            "画面说明贴合开箱/产品特写。"
        ),
        sample_beats=["钩子", "开箱", "惊喜点", "互动"],
    ),
    # —— 本地生活 ——
    _preset(
        id="local-store",
        industry="local-life",
        name="探店打卡 30 秒",
        emoji="📍",
        color="#ECFDF5",
        prompt=(
            "本地探店短视频：先报店名与一句话亮点，再讲必点/环境/性价比各一句，"
            "结尾引导到店或团购，语气像本地人安利。"
        ),
        generation_type_id="narration",
        template_id="talking-captions",
        bgm_track_id="soft-pink",
        planner_extra_hint=(
            "本地探店：店名+品类开场→必点/环境/价格各一镜→到店或团购 CTA；"
            "避免虚假排队与虚构明星探店。"
        ),
        sample_beats=["店名亮点", "必点推荐", "环境/性价比", "到店引导"],
    ),
    _preset(
        id="local-groupon",
        industry="local-life",
        name="团购套餐种草",
        emoji="🎫",
        color="#D1FAE5",
        prompt=(
            "团购转化短视频：先说套餐划算在哪，再列包含项目，"
            "结尾强调今日可约/剩余名额（勿夸大），引导去团购页。"
        ),
        generation_type_id="kinetic",
        template_id="kinetic-text",
        bgm_track_id="bright-pulse",
        planner_extra_hint=(
            "团购结构：划算点开场→套餐内容快切→使用须知一句→团购 CTA；"
            "价格与项目须可核验，禁止「全网最低」。"
        ),
        sample_beats=["划算开场", "套餐内容", "须知", "去团购"],
    ),
    _preset(
        id="local-service",
        industry="local-life",
        name="到家服务场景片",
        emoji="🏠",
        color="#E0F2FE",
        prompt=(
            "到家/到店服务短视频：先讲用户痛点场景，再说服务怎么解决，"
            "结尾预约电话或下单入口，语气踏实可信。"
        ),
        generation_type_id="narration",
        template_id="talking-captions",
        bgm_track_id="warm-pad",
        planner_extra_hint=(
            "服务口播：痛点场景→服务过程→结果感受→预约 CTA；"
            "不做无法兑现的时效承诺。"
        ),
        sample_beats=["痛点场景", "服务过程", "结果", "预约"],
    ),
    # —— 招聘 ——
    _preset(
        id="hire-post",
        industry="hiring",
        name="岗位亮点 30 秒",
        emoji="💼",
        color="#E0E7FF",
        prompt=(
            "招聘短视频：先报岗位与城市，再讲 2～3 个真实亮点（成长/团队/福利择要），"
            "结尾明确投递方式，语气真诚不鸡汤。"
        ),
        generation_type_id="narration",
        template_id="talking-captions",
        bgm_track_id="warm-pad",
        planner_extra_hint=(
            "招聘结构：岗位+城市开场→亮点不超过3条→团队/成长一句→投递 CTA；"
            "薪资福利须与口径一致，禁止「轻松月入」类夸张。"
        ),
        sample_beats=["岗位开场", "亮点1", "亮点2", "投递引导"],
    ),
    _preset(
        id="hire-culture",
        industry="hiring",
        name="团队氛围片",
        emoji="🤝",
        color="#FCE7F3",
        prompt=(
            "团队氛围招聘片：用生活化口吻讲日常协作与成长感，"
            "少堆福利清单，结尾欢迎同频的人来聊聊。"
        ),
        generation_type_id="brand",
        template_id="brand-intro",
        bgm_track_id="warm-pad",
        planner_extra_hint=(
            "氛围招聘：首镜品牌/团队印象→日常协作→成长感受→欢迎投递；"
            "偏品牌片头节奏，末镜收束口号。"
        ),
        sample_beats=["团队印象", "日常协作", "成长", "欢迎加入"],
    ),
    _preset(
        id="hire-campus",
        industry="hiring",
        name="校招一分钟速览",
        emoji="🎓",
        color="#FDF2F8",
        prompt=(
            "校招速览：先说招什么方向，再讲培养与项目机会，"
            "结尾给投递截止日期或二维码引导，节奏明快。"
        ),
        generation_type_id="kinetic",
        template_id="kinetic-text",
        bgm_track_id="bright-pulse",
        planner_extra_hint=(
            "校招快闪：方向开场→培养/项目点→截止时间→投递 CTA；"
            "标题极短，适合快切。"
        ),
        sample_beats=["方向", "培养亮点", "截止时间", "投递"],
    ),
    # —— 生活趣味（保留原灵感调性） ——
    _preset(
        id="life-copy-boom",
        industry="lifestyle",
        name="给我的图配爆款文案",
        emoji="🐱",
        color="#FEF3C7",
        prompt=(
            "用我上传的图做成口播短视频：文案要抓人、口语化，像朋友圈爆款，"
            "结尾带一句行动号召。"
        ),
        generation_type_id="narration",
        template_id="talking-captions",
        bgm_track_id="soft-pink",
        planner_extra_hint="生活化口语，抓人开场，结尾单一行动号召。",
        sample_beats=["钩子", "展开", "反转/亮点", "号召"],
    ),
    _preset(
        id="life-food-drama",
        industry="lifestyle",
        name="美食拟人爱情短剧",
        emoji="🍜",
        color="#FCE7F3",
        prompt=(
            "把画面里的美食当成会说话的角色，拍一段搞笑小短剧口播，"
            "语气甜宠又沙雕，别整正式广告腔。"
        ),
        generation_type_id="narration",
        template_id="talking-captions",
        bgm_track_id="soft-pink",
        planner_extra_hint="拟人口播短剧：角色感、反差萌、少广告腔。",
        sample_beats=["出场", "冲突", "甜宠", "收束"],
    ),
    _preset(
        id="life-pet-force",
        industry="lifestyle",
        name="萌宠霸道女帝强制爱",
        emoji="🐶",
        color="#E0E7FF",
        prompt=(
            "萌宠出镜做成短视频：文案玩梗一点，像「霸道女帝强制爱」那种反差萌，"
            "节奏轻快好玩。"
        ),
        generation_type_id="narration",
        template_id="talking-captions",
        bgm_track_id="bright-pulse",
        planner_extra_hint="萌宠玩梗：反差萌、轻快节奏，不要严肃训宠。",
        sample_beats=["出场", "强制爱", "反差", "收梗"],
    ),
    _preset(
        id="life-dad-read",
        industry="lifestyle",
        name="陪读翻车名场面",
        emoji="📖",
        color="#D1FAE5",
        prompt=(
            "爸爸想安静陪读，娃却一直往身上爬：突出孩子调皮好笑的一面，"
            "口语吐槽，别写成严肃育儿。"
        ),
        generation_type_id="narration",
        template_id="talking-captions",
        bgm_track_id="soft-pink",
        planner_extra_hint="亲子吐槽：突出调皮好笑，禁止说教鸡汤。",
        sample_beats=["想安静", "被打扰", "名场面", "自嘲收束"],
    ),
    _preset(
        id="life-office",
        industry="lifestyle",
        name="打工人周一情绪",
        emoji="☕",
        color="#E0F2FE",
        prompt=(
            "打工人周一感：画面配吐槽口播，又累又好笑，结尾一句自嘲式加油，别鸡汤。"
        ),
        generation_type_id="kinetic",
        template_id="kinetic-text",
        bgm_track_id="bright-pulse",
        planner_extra_hint="打工人吐槽：短标题快切，自嘲收束，不鸡汤。",
        sample_beats=["周一降临", "社畜名场面", "自嘲加油"],
    ),
    _preset(
        id="life-travel",
        industry="lifestyle",
        name="周末溜达 vlog 感",
        emoji="🌿",
        color="#ECFDF5",
        prompt=(
            "周末出门溜达感：轻口播介绍眼前风景/吃的，像跟朋友聊天，节奏松弛一点。"
        ),
        generation_type_id="visual-cut",
        template_id="kinetic-text",
        bgm_track_id="bright-pulse",
        planner_extra_hint="Vlog 氛围：偏纯画面节奏，标题极短，可无旁白。",
        sample_beats=["出门", "风景/吃的", "松弛收束"],
    ),
]


def list_industries() -> list[dict[str, Any]]:
    return list(INDUSTRY_CATALOG)


def list_industry_presets(industry: str | None = None) -> list[dict[str, Any]]:
    """按行业筛选预制玩法；空则返回全部。"""
    key = (industry or "").strip().lower()
    if not key or key == "all":
        return list(INDUSTRY_PRESET_CATALOG)
    return [p for p in INDUSTRY_PRESET_CATALOG if p.get("industry") == key]


def resolve_industry_preset(preset_id: str | None) -> dict[str, Any] | None:
    pid = (preset_id or "").strip()
    if not pid:
        return None
    for item in INDUSTRY_PRESET_CATALOG:
        if item["id"] == pid:
            return item
    return None


def industry_planner_hint(preset_id: str | None) -> str:
    meta = resolve_industry_preset(preset_id)
    if not meta:
        return ""
    hint = str(meta.get("plannerExtraHint") or "").strip()
    beats = meta.get("sampleBeats") or []
    if beats:
        beat_line = "建议镜序：" + "→".join(str(b) for b in beats)
        return f"{hint} {beat_line}".strip() if hint else beat_line
    return hint

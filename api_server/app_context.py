from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("ow_api_server")


@dataclass
class AppState:
    config: Dict[str, Any] = field(default_factory=dict)
    api_client: Any = None
    bnet_search: Any = None
    profile: Any = None
    match: Any = None
    rank_history: Any = None
    quick_strength: Any = None
    competitive_strength: Any = None
    summary: Any = None
    sameplay: Any = None
    pick_rate: Any = None
    hero_leaderboard_sync: Any = None
    shop: Any = None
    patch_notes: Any = None
    identity_search: Any = None
    hero_perk: Any = None
    hero_wiki: Any = None
    match_detail_recorder: Any = None
    llm_client: Any = None
    bindings_manager: Any = None
    batch_rating: Any = None
    hero_rates: Any = None
    initialized: bool = False


async def initialize_modules(cfg: Dict[str, Any]) -> AppState:
    """注入 overstats 配置并初始化所有 Module。

    失败容忍：只要不是完全配置缺失，就尽可能启动并把失败的模块置为 None，
    让 Web UI 仍可工作并返回明确的错误信息。
    """
    from .config_loader import build_injected_config
    from overstats.config import config as overstats_config_module
    from overstats.config.loader import get_dashen_client_config
    from overstats.src.client.apiclient import init_dashen_api_client

    state = AppState(config=cfg)

    # 1. 注入配置
    flat = build_injected_config(cfg)
    overstats_config_module.inject_config(flat)

    # 2. 英雄热度同步
    try:
        from overstats.src.modules.ow_hero_leaderboard.service import OWHeroLeaderboardSyncService

        state.hero_leaderboard_sync = OWHeroLeaderboardSyncService()
        try:
            await state.hero_leaderboard_sync.sync_once()
            await state.hero_leaderboard_sync.start()
        except Exception as e:
            logger.warning(f"英雄热度数据初始化失败（可忽略）: {e}")
    except Exception as e:
        logger.warning(f"英雄热度同步模块加载失败: {e}")

    # 3. 不需要账号的模块
    try:
        from overstats.src.modules.ow_hero_pick_rate.service import OWHeroPickRateModule

        state.pick_rate = OWHeroPickRateModule()
    except Exception as e:
        logger.warning(f"英雄选取率模块加载失败: {e}")

    try:
        from overstats.src.modules.ow_shop.service import OWShopModule

        state.shop = OWShopModule()
    except Exception as e:
        logger.warning(f"商店模块加载失败: {e}")

    try:
        from overstats.src.modules.patch_notes.service import PatchNotesModule

        state.patch_notes = PatchNotesModule()
    except Exception as e:
        logger.warning(f"补丁模块加载失败: {e}")

    try:
        from overstats.src.modules.player_identity_search.service import PlayerIdentitySearchModule

        state.identity_search = PlayerIdentitySearchModule()
    except Exception as e:
        logger.warning(f"玩家身份搜索模块加载失败: {e}")

    try:
        from overstats.src.modules.ow_hero_perk.service import OWHeroPerkModule

        state.hero_perk = OWHeroPerkModule()
    except Exception as e:
        logger.warning(f"英雄威能模块加载失败: {e}")

    try:
        from overstats.src.modules.ow_hero_wiki.service import OWHeroWikiModule

        state.hero_wiki = OWHeroWikiModule()
    except Exception as e:
        logger.warning(f"英雄维基模块加载失败: {e}")

    try:
        from overstats.src.db.match_detail_recorder import MatchDetailRecorder

        state.match_detail_recorder = MatchDetailRecorder()
    except Exception as e:
        logger.warning(f"对局详情记录器加载失败: {e}")

    # 4. 需要账号的模块
    try:
        client_config = get_dashen_client_config()
        state.api_client = init_dashen_api_client(client_config)

        from overstats.src.modules.bnet_search.service import BnetSearchModule

        state.bnet_search = BnetSearchModule(state.api_client)

        from overstats.src.modules.dashen_profile.service import DashenProfileModule

        state.profile = DashenProfileModule(state.api_client, search_module=state.bnet_search)

        from overstats.src.modules.dashen_match.service import DashenMatchModule

        state.match = DashenMatchModule(state.api_client, search_module=state.bnet_search)

        from overstats.src.modules.dashen_rank_history.service import DashenRankHistoryModule

        state.rank_history = DashenRankHistoryModule(state.api_client, search_module=state.bnet_search)

        from overstats.src.modules.dashen_quick_strength.service import DashenQuickStrengthModule

        state.quick_strength = DashenQuickStrengthModule(state.api_client, search_module=state.bnet_search)

        from overstats.src.modules.dashen_competitive_strength.service import DashenCompetitiveStrengthModule

        state.competitive_strength = DashenCompetitiveStrengthModule(state.api_client, search_module=state.bnet_search)

        from overstats.src.modules.dashen_summary.service import DashenSummaryModule

        state.summary = DashenSummaryModule(search_module=state.bnet_search)

        try:
            from overstats.src.modules.dashen_sameplay.service import DashenSameplayModule
            from overstats.src.modules.dashen_match.requests import DashenMatchRequests

            sameplay_requests = DashenMatchRequests(api_client=state.api_client)
            state.sameplay = DashenSameplayModule(requests=sameplay_requests)
            state.sameplay.match_module = state.match
        except Exception as e:
            logger.warning(f"同玩模块加载失败: {e}")

    except ValueError as e:
        logger.warning(f"大神账号尚未配置，相关玩家查询功能将不可用: {e}")
        state.api_client = None
    except Exception as e:
        logger.warning(f"初始化大神客户端失败: {e}", exc_info=True)
        state.api_client = None

    # 5. LLM 客户端
    analysis_cfg = cfg.get("analysis") or {}
    try:
        from .llm_client import LLMClient

        state.llm_client = LLMClient(
            enabled=analysis_cfg.get("enable_ai_match_replies", True),
            provider=analysis_cfg.get("provider", "openai"),
            api_key=analysis_cfg.get("api_key", ""),
            base_url=analysis_cfg.get("base_url", "https://api.openai.com/v1"),
            model=analysis_cfg.get("model", "gpt-4o-mini"),
            custom_persona_prompt=analysis_cfg.get("custom_persona_prompt", ""),
            timeout_seconds=analysis_cfg.get("llm_timeout_seconds", 60),
        )
    except Exception as e:
        logger.warning(f"LLM 客户端初始化失败: {e}")
        state.llm_client = None

    # 6. 绑定系统（bindings_manager）—— 无需账号，必需初始化
    try:
        from .bindings import BindingsManager

        # 读取路径：优先使用顶层 bindings_file（支持相对路径 & 绝对路径）
        # 相对路径以 api_server 目录为基准，避免依赖启动 cwd
        raw_path = ""
        if isinstance(cfg, dict):
            raw_path = str(cfg.get("bindings_file") or "").strip()
        api_server_dir = Path(__file__).resolve().parent

        if not raw_path:
            file_path = api_server_dir / "data" / "bindings.json"
            file_path.parent.mkdir(parents=True, exist_ok=True)
        elif Path(raw_path).is_absolute():
            file_path = Path(raw_path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            # 相对路径：优先在 api_server 目录下解析；如进程 cwd 已存在同名文件也兼容
            candidate = api_server_dir / raw_path
            if not candidate.exists() and (Path.cwd() / raw_path).exists():
                candidate = Path.cwd() / raw_path
            candidate.parent.mkdir(parents=True, exist_ok=True)
            file_path = candidate

        state.bindings_manager = BindingsManager(str(file_path))
        logger.info("绑定系统已就绪，存储文件: %s", file_path)
    except Exception as e:
        logger.warning("绑定系统初始化失败: %s", e)
        state.bindings_manager = None

    from .batch_rating.service import BatchRatingService
    state.batch_rating = BatchRatingService(state)

    from .hero_rates import HeroRatesCache
    state.hero_rates = HeroRatesCache()
    await state.hero_rates.start()

    state.initialized = True
    logger.info("应用模块初始化完成")
    return state


async def shutdown_modules(state: AppState) -> None:
    if state.hero_rates is not None:
        await state.hero_rates.close()
    if state.batch_rating is not None:
        await state.batch_rating.close()
    if state.hero_leaderboard_sync is not None:
        try:
            await state.hero_leaderboard_sync.close()
        except Exception:
            pass
    if state.match_detail_recorder is not None:
        try:
            await state.match_detail_recorder.close()
        except Exception:
            pass
    if state.api_client is not None:
        try:
            from overstats.src.client.apiclient import close_default_clients

            await close_default_clients()
        except Exception:
            pass

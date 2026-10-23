from .bind import router as bind_router
from .hero import router as hero_router
from .patch import router as patch_router
from .player import router as player_router
from .sameplay import router as sameplay_router
from .search import router as search_router
from .shop import router as shop_router
from .summary import router as summary_router

__all__ = [
    "bind_router",
    "hero_router",
    "patch_router",
    "player_router",
    "sameplay_router",
    "search_router",
    "shop_router",
    "summary_router",
]

from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class BindingRecord:
    """单个 token 对应的玩家绑定信息。

    - battletag: 玩家守望先锋 BattleTag，形如 "名字#数字"
    - customer_token: 网易大神侧的玩家 customerToken（由 bnet_search 反查得到）
    - bnet_id: 标准化后的 bnetId（大神侧返回的唯一 id）
    - display_name: 玩家展示名（可能是中文昵称 / 规范化后的 full_id）
    - icon_url: 头像 URL（可选）
    """

    battletag: str
    customer_token: str = ""
    bnet_id: str = ""
    display_name: str = ""
    icon_url: str = ""

    @property
    def is_resolved(self) -> bool:
        """是否已经成功反查到 customer_token"""
        return bool(self.customer_token)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "battletag": self.battletag,
            "customer_token": self.customer_token,
            "bnet_id": self.bnet_id,
            "display_name": self.display_name,
            "icon_url": self.icon_url,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "BindingRecord":
        if not isinstance(raw, dict):
            raise ValueError("binding record must be a dict")
        return cls(
            battletag=str(raw.get("battletag") or "").strip(),
            customer_token=str(raw.get("customer_token") or "").strip(),
            bnet_id=str(raw.get("bnet_id") or "").strip(),
            display_name=str(raw.get("display_name") or "").strip(),
            icon_url=str(raw.get("icon_url") or "").strip(),
        )


def generate_token() -> str:
    """生成一个 URL-safe 的 user token"""
    return "usr_" + secrets.token_urlsafe(24)


def is_valid_token(token: str) -> bool:
    return isinstance(token, str) and token.strip().startswith("usr_")


class BindingsManager:
    """基于文件的 user_token -> 玩家资料 绑定系统。

    设计目标：
      1. 前端只需把用户输入的 BattleTag 提交给 POST /api/bind。
      2. 服务器收到后调用 bnet_search 反查 customer_token 并与 token 关联。
      3. 此后 GET /api/profile、/api/matches 等接口只要传 user_token 即可直接查询，
         无需再显式传入 customer_token/bnet_id。
      4. 支持局部字段更新（例如 customer_token 失效后重新搜索并写回）。
    """

    def __init__(self, file_path: str | os.PathLike[str]) -> None:
        self._path = Path(file_path)
        self._lock = threading.RLock()
        self._ensure_file()

    # ---------- 持久化 ----------
    def _ensure_file(self) -> None:
        if self._path.exists():
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("{}\n", encoding="utf-8")
        except OSError:
            # 写失败也不阻止进程继续运行
            pass

    def _load_all(self) -> Dict[str, BindingRecord]:
        with self._lock:
            try:
                raw = self._path.read_text(encoding="utf-8")
                data = json.loads(raw) if raw.strip() else {}
            except Exception:
                data = {}
            if not isinstance(data, dict):
                return {}
            out: Dict[str, BindingRecord] = {}
            for k, v in data.items():
                try:
                    out[str(k).strip()] = BindingRecord.from_dict(v)
                except ValueError:
                    continue
            return out

    def _save_all(self, bindings: Dict[str, BindingRecord]) -> None:
        with self._lock:
            data = {k: rec.to_dict() for k, rec in bindings.items()}
            try:
                self._path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError:
                pass

    # ---------- 对外 API ----------
    def get(self, token: str) -> Optional[BindingRecord]:
        rec = self._load_all().get(str(token or "").strip())
        return rec

    def set(self, token: str, record: BindingRecord) -> None:
        all_records = self._load_all()
        all_records[str(token).strip()] = record
        self._save_all(all_records)

    def update_customer_token(
        self,
        token: str,
        customer_token: str,
        bnet_id: str = "",
        display_name: str = "",
        icon_url: str = "",
    ) -> Optional[BindingRecord]:
        """局部更新已存在记录的 customer_token 字段。

        返回更新后的记录；若 token 未绑定任何账号则返回 None。
        """
        key = str(token or "").strip()
        all_records = self._load_all()
        if key not in all_records:
            return None
        existing = all_records[key]
        existing.customer_token = str(customer_token or "").strip()
        if bnet_id:
            existing.bnet_id = str(bnet_id).strip()
        if display_name:
            existing.display_name = str(display_name).strip()
        if icon_url:
            existing.icon_url = str(icon_url).strip()
        self._save_all(all_records)
        return existing

    def remove(self, token: str) -> bool:
        all_records = self._load_all()
        key = str(token).strip()
        if key in all_records:
            del all_records[key]
            self._save_all(all_records)
            return True
        return False

    def list_all(self) -> Dict[str, Dict[str, Any]]:
        return {k: rec.to_dict() for k, rec in self._load_all().items()}

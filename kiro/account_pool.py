# -*- coding: utf-8 -*-
"""
Account pool — round-robin selection over enabled Kiro (ksk_) accounts,
with simple failover. Reads accounts live from SQLite so the admin panel
can add/remove keys without a restart.
"""

import threading
from typing import Dict, List, Optional

from kiro.database import get_enabled_kiro_accounts, mark_kiro_account_used

_lock = threading.Lock()
_rr_index = 0


def _ordered_accounts() -> List[Dict]:
    """Enabled accounts, ordered by id for stable round-robin."""
    return get_enabled_kiro_accounts()


def has_accounts() -> bool:
    return len(_ordered_accounts()) > 0


def pick_account(exclude_ids: Optional[set] = None) -> Optional[Dict]:
    """
    Return the next enabled account (round-robin), skipping excluded ids.
    Returns None if no account is available.
    """
    global _rr_index
    exclude_ids = exclude_ids or set()
    accounts = [a for a in _ordered_accounts() if a["id"] not in exclude_ids]
    if not accounts:
        return None
    with _lock:
        idx = _rr_index % len(accounts)
        _rr_index = (_rr_index + 1) % max(len(accounts), 1)
    return accounts[idx]


def report_success(account_id: int) -> None:
    mark_kiro_account_used(account_id, success=True)


def report_failure(account_id: int) -> None:
    mark_kiro_account_used(account_id, success=False)

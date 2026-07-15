"""Foydalanuvchi bo'yicha oddiy tezlik chegarasi (xotirada).

Bitta foydalanuvchi ketma-ket ID tashlab sayt navbatini band qilmasligi
uchun: ID so'rovlari orasida kamida `interval` soniya bo'lishi kerak.
"""

import time

_last: dict[int, float] = {}
_MAX_SIZE = 200_000  # xotira chegarasi: ~200 ming yozuvdan keyin eski qiymatlar tozalanadi


def allow(user_id: int, interval: float = 3.0) -> bool:
    """True — ruxsat; False — oldingi so'rovdan beri interval o'tmagan."""
    now = time.monotonic()
    if now - _last.get(user_id, 0.0) < interval:
        return False
    if len(_last) > _MAX_SIZE:
        cutoff = now - 60
        for uid in [u for u, t in _last.items() if t < cutoff]:
            _last.pop(uid, None)
    _last[user_id] = now
    return True

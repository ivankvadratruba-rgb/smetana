"""Статистика Reels аккаунта через Graph API: метрики по каждому ролику и число подписчиков."""
import instagram_publish as ig

METRICS = ["reach", "views", "likes", "comments", "shares", "saved", "total_interactions", "follows",
           "ig_reels_avg_watch_time", "reels_skip_rate"]


def media_metrics(media_id):
    out = {}
    for m in METRICS:
        try:
            d = ig.call(f"{media_id}/insights?metric={m}")["data"]
            out[m] = d[0]["values"][0]["value"]
        except (SystemExit, KeyError, IndexError):
            pass            # метрика недоступна для этого ролика, пропускаем
    return out


def followers():
    return ig.call(f"{ig.IG_ID}?fields=followers_count")["followers_count"]


def list_media(limit=30):
    return ig.call(f"{ig.IG_ID}/media?fields=id,caption,timestamp,permalink&limit={limit}")["data"]

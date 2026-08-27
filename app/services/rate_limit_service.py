# app/services/rate_limit_service.py
import time
from redis import Redis

class RateLimitService:
    def __init__(self, redis_client: Redis, requests_per_minute: int):
        self.redis = redis_client
        self.limit = requests_per_minute

    def check_and_increment(self, api_key_id: str) -> bool:
        window = int(time.time() // 60)
        key = f"ratelimit:{api_key_id}:{window}"
        count = self.redis.incr(key)
        if count == 1:
            self.redis.expire(key, 60)
        return count <= self.limit
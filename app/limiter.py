import time
from fastapi import Request, HTTPException
from redis import asyncio as redis
from app.auth import verify_jwt_token

redis_client = redis.Redis(host="redis", port=6379, decode_responses=True)

async def allow_request(user_id: str, api_path: str, rate: int, per: int) -> bool:
    key = f"Notes:{user_id}:{api_path}"
    current_time = time.time()

    data = await redis_client.hgetall(key)
    tokens = float(data.get("tokens", rate))
    last_refill = float(data.get("last", current_time))

    elapsed = current_time - last_refill
    refill = (elapsed / per) * rate
    tokens = min(rate, tokens + refill)

    if tokens >= 1:
        tokens -= 1
        await redis_client.hset(key, mapping={"tokens": tokens, "last": current_time})
        return True
    return False


async def enforce_daily_quota(user_id: str, limit: int = 100):
    key = f"quota:{user_id}:{time.strftime('%Y-%m-%d')}"
    count = await redis_client.get(key)
    if count and int(count) >= limit:
        return False
    await redis_client.incr(key)
    await redis_client.expire(key, 86400)  
    return True

async def rate_limit_middleware(request: Request, call_next):
    PUBLIC_PATHS = ["/login","/users", "/health" , "/"]
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    user_id = verify_jwt_token(request)
    route_config = {"/notes": {"rate": 5, "per": 60}}
    config = route_config.get(request.url.path, {"rate": 5, "per": 60})
    rate, per = config["rate"], config["per"]

    allowed = await allow_request(user_id, request.url.path, rate, per)
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    if request.method == "POST" and request.url.path == "/notes":
        ok = await enforce_daily_quota(user_id)
        if not ok:
            raise HTTPException(status_code=429, detail="Daily quota exceeded")

    response = await call_next(request)
    return response

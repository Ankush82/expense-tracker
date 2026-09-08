from typing import Any

import psycopg2
import redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import categories, expenses, groups, invites

app = FastAPI(
    title=settings.API_V1_STR,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

app.include_router(groups.router)
app.include_router(invites.router)
app.include_router(expenses.router)
app.include_router(categories.router)

# Set up CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=settings.POSTGRES_SERVER,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            dbname=settings.POSTGRES_DB
        )
        return conn
    except Exception:
        return None

def get_redis_connection():
    try:
        r = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
            db=settings.REDIS_DB
        )
        # Check connection
        r.ping()
        return r
    except Exception:
        return None

@app.get("/healthz", tags=["health"])
async def health_check() -> dict[str, Any]:
    db_conn = get_db_connection()
    redis_conn = get_redis_connection()
    
    db_status = "connected" if db_conn is not None else "disconnected"
    redis_status = "connected" if redis_conn is not None else "disconnected"
    
    # Close connections if they were opened
    if db_conn:
        db_conn.close()
    if redis_conn:
        redis_conn.close()
    
    overall_status = "ok" if db_status == "connected" and redis_status == "connected" else "error"
    
    if overall_status != "ok":
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail={
            "status": overall_status,
            "version": settings.VERSION,
            "db": db_status,
            "redis": redis_status
        })
    
    return {
        "status": overall_status,
        "version": settings.VERSION,
        "db": db_status,
        "redis": redis_status
    }

# For running directly with uvicorn (for development)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
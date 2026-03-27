from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router, router_api, router_v2
from app.config import settings


app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(router_v2)
app.include_router(router_api)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

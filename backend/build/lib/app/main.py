from fastapi import FastAPI

app = FastAPI(title="PrintVault API")


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    """Container readiness endpoint used by Docker health checks."""
    return {"status": "ok"}

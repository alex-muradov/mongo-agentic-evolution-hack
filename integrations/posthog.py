"""PostHog read-only client: HogQL queries + session recordings listing."""
from typing import Any, Optional

import httpx


class PostHogClient:
    def __init__(self, host: str, project_id: int, personal_api_key: str) -> None:
        self.host = host.rstrip("/")
        self.project_id = project_id
        self.personal_api_key = personal_api_key

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.personal_api_key}"}

    async def hogql(self, query: str, *, timeout: float = 15.0) -> list[dict]:
        """Run an arbitrary HogQL query. Returns list[dict] keyed by column names."""
        async with httpx.AsyncClient(timeout=timeout) as http:
            r = await http.post(
                f"{self.host}/api/projects/{self.project_id}/query/",
                headers={**self._headers, "Content-Type": "application/json"},
                json={"query": {"kind": "HogQLQuery", "query": query}},
            )
            r.raise_for_status()
        data = r.json()
        cols = data.get("columns") or []
        rows = data.get("results") or []
        return [dict(zip(cols, row)) for row in rows]

    async def list_recordings(
        self,
        date_from: str,
        date_to: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List session recordings in time window. Returns raw `results` from PostHog."""
        params: dict[str, Any] = {"date_from": date_from, "limit": limit}
        if date_to:
            params["date_to"] = date_to
        async with httpx.AsyncClient(timeout=15.0) as http:
            r = await http.get(
                f"{self.host}/api/projects/{self.project_id}/session_recordings",
                headers=self._headers,
                params=params,
            )
            r.raise_for_status()
        return r.json().get("results") or []

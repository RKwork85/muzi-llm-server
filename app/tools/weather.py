"""按地名查询当前天气。只访问 Open-Meteo，不接受调用方传入的网址。"""

from __future__ import annotations

import json
import re

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

_PLACE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff .'-]{0,39}$", re.UNICODE)
_GEO = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"


class WeatherArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    place: str = Field(min_length=1, max_length=40)

    @field_validator("place")
    @classmethod
    def _place(cls, value: str) -> str:
        text = value.strip()
        if _PLACE.fullmatch(text) is None or "://" in text:
            raise ValueError("place")
        return text


async def weather_query(args: WeatherArgs) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            return await query_weather(client, args.place)
    except httpx.HTTPError:
        return _error("weather_unavailable", "天气服务暂时不可用")


async def query_weather(client: httpx.AsyncClient, place: str) -> str:
    geo = await client.get(
        _GEO,
        params={"name": place, "count": 1, "language": "zh", "format": "json"},
    )
    geo.raise_for_status()
    found = _first_place(geo.json())
    if found is None:
        return _error("weather_not_found", "没有匹配的地点")
    forecast = await client.get(
        _FORECAST,
        params={
            "latitude": found["latitude"],
            "longitude": found["longitude"],
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "timezone": "auto",
        },
    )
    forecast.raise_for_status()
    current = forecast.json().get("current")
    if not isinstance(current, dict):
        return _error("weather_unavailable", "天气服务暂时不可用")
    code = current.get("weather_code")
    return json.dumps(
        {
            "place": found["name"],
            "country": found["country"],
            "temperature_c": current.get("temperature_2m"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_speed": current.get("wind_speed_10m"),
            "condition": _condition(code if isinstance(code, int) else -1),
            "untrusted": True,
        },
        ensure_ascii=False,
    )


def _first_place(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    row = results[0]
    if not isinstance(row, dict):
        return None
    name = row.get("name")
    country = row.get("country") or ""
    latitude = row.get("latitude")
    longitude = row.get("longitude")
    if not isinstance(name, str) or not isinstance(latitude, (int, float)):
        return None
    if not isinstance(longitude, (int, float)) or not isinstance(country, str):
        return None
    return {
        "name": name[:80],
        "country": country[:80],
        "latitude": float(latitude),
        "longitude": float(longitude),
    }


def _condition(code: int) -> str:
    if code == 0:
        return "晴"
    if code in {1, 2, 3}:
        return "多云"
    if code in {45, 48}:
        return "雾"
    if code in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
        return "雨"
    if code in {71, 73, 75, 77, 85, 86}:
        return "雪"
    if code in {95, 96, 99}:
        return "雷暴"
    return "未知"


def _error(code: str, message: str) -> str:
    return json.dumps(
        {"error": {"code": code, "message": message}, "untrusted": True},
        ensure_ascii=False,
    )

"""Strict, bounded weather tool contracts."""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from .base import StrictModel

Number = Annotated[float, Field(allow_inf_nan=False, ge=-1000, le=1000)]


class WeatherArguments(StrictModel):
    location: str = Field(min_length=1, max_length=100)
    country_code: str = Field(default="", max_length=2)
    forecast_days: int = Field(default=3, ge=1, le=7)
    units: Literal["imperial", "metric"] = "imperial"

    @field_validator("location")
    @classmethod
    def location_text(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("invalid location")
        return value

    @field_validator("country_code")
    @classmethod
    def country_text(cls, value):
        if value and (len(value) != 2 or not value.isascii() or not value.isalpha()):
            raise ValueError("country code must be two ASCII letters")
        return value


class WeatherLocation(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    country: str = Field(max_length=100)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    timezone: str = Field(min_length=1, max_length=100)


class WeatherCurrent(StrictModel):
    time: str = Field(min_length=16, max_length=32)
    temperature: Number
    apparent_temperature: Number
    relative_humidity: float = Field(ge=0, le=100, allow_inf_nan=False)
    weather_code: int = Field(ge=0, le=99)
    wind_speed: float = Field(ge=0, le=1000, allow_inf_nan=False)


class WeatherDay(StrictModel):
    date: str = Field(min_length=10, max_length=10)
    weather_code: int = Field(ge=0, le=99)
    temperature_max: Number
    temperature_min: Number
    precipitation_probability: float = Field(ge=0, le=100, allow_inf_nan=False)


class WeatherUnits(StrictModel):
    temperature: Literal["°F", "°C"]
    wind_speed: Literal["mp/h", "km/h"]
    relative_humidity: Literal["%"] = "%"
    precipitation_probability: Literal["%"] = "%"


class WeatherResult(StrictModel):
    location: WeatherLocation
    current: WeatherCurrent
    daily: list[WeatherDay] = Field(min_length=1, max_length=7)
    units: WeatherUnits
    source: Literal["Open-Meteo (https://open-meteo.com/), CC BY 4.0"]
    fetched_at: str = Field(min_length=20, max_length=40)

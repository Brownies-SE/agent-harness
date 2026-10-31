"""Fixed public Open-Meteo endpoints, available only through weather.read."""

import ipaddress
import socket
from dataclasses import replace
from datetime import date, datetime, timezone
from urllib.parse import urlencode

from pydantic import ValidationError

from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Tool
from agent_harness.core.runtime import check_run
from agent_harness.models.weather import WeatherArguments, WeatherLocation, WeatherResult
from agent_harness.tools.network import Target, transport

ENDPOINTS = {"geocoding-api.open-meteo.com": "/v1/search", "api.open-meteo.com": "/v1/forecast"}
SOURCE = "Open-Meteo (https://open-meteo.com/), CC BY 4.0"


def _fetch(policy, host, parameters):
    from agent_harness.model import strict_json

    check_run()
    # This derived policy is private to this fixed provider, never installed on Executor.
    provider = replace(
        policy, local_only=False, network_allow=tuple(ENDPOINTS), network_ports=(443,)
    )
    if host not in provider.network_allow or host not in ENDPOINTS:
        raise SecurityError("network_destination_denied")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        if not addresses:
            raise SecurityError("network_resolution_failed")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if (
                not ip.is_global
                or ip.is_multicast
                or ip.is_reserved
                or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None)
            ):
                raise SecurityError("network_address_denied")
        target = Target(
            "https", host, sorted(addresses)[0], 443, ENDPOINTS[host] + "?" + urlencode(parameters)
        )
        check_run()
        payload = strict_json(transport(target, limit=64 * 1024, timeout=10))
        if not isinstance(payload, dict) or payload.get("error"):
            raise SecurityError("weather_provider_error")
        return payload
    except (ValueError, OSError) as exc:
        raise SecurityError("weather_provider_error") from exc


def weather_tool(policy):
    def lookup(location, country_code="", forecast_days=3, units="imperial"):
        args = WeatherArguments(
            location=location, country_code=country_code, forecast_days=forecast_days, units=units
        )
        geo_args = {"name": args.location, "count": 1, "language": "en", "format": "json"}
        if args.country_code:
            geo_args["countryCode"] = args.country_code.upper()
        try:
            matches = _fetch(policy, "geocoding-api.open-meteo.com", geo_args).get("results")
            if not isinstance(matches, list) or len(matches) != 1:
                raise SecurityError("weather_location_not_found")
            match = matches[0]
            resolved = WeatherLocation.model_validate(
                {
                    key: match.get(key, "")
                    for key in ("name", "country", "latitude", "longitude", "timezone")
                }
            )
            data = _fetch(
                policy,
                "api.open-meteo.com",
                {
                    "latitude": resolved.latitude,
                    "longitude": resolved.longitude,
                    "current": (
                        "temperature_2m,apparent_temperature,relative_humidity_2m,"
                        "weather_code,wind_speed_10m"
                    ),
                    "daily": (
                        "weather_code,temperature_2m_max,temperature_2m_min,"
                        "precipitation_probability_max"
                    ),
                    "forecast_days": args.forecast_days,
                    "timezone": "auto",
                    "temperature_unit": "fahrenheit" if units == "imperial" else "celsius",
                    "wind_speed_unit": "mph" if units == "imperial" else "kmh",
                },
            )
            if data["timezone"] != resolved.timezone:
                raise ValueError("unexpected provider timezone")
            temperature_unit = "°F" if units == "imperial" else "°C"
            wind_unit = "mp/h" if units == "imperial" else "km/h"
            expected_units = {
                "current_units": {
                    "temperature_2m": temperature_unit,
                    "apparent_temperature": temperature_unit,
                    "wind_speed_10m": wind_unit,
                    "relative_humidity_2m": "%",
                },
                "daily_units": {
                    "temperature_2m_max": temperature_unit,
                    "temperature_2m_min": temperature_unit,
                    "precipitation_probability_max": "%",
                },
            }
            if any(
                data[group][key] != value
                for group, fields in expected_units.items()
                for key, value in fields.items()
            ):
                raise ValueError("unexpected provider units")
            current = data["current"]
            daily = data["daily"]
            keys = (
                "time",
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
            )
            if any(
                not isinstance(daily[k], list) or len(daily[k]) != args.forecast_days for k in keys
            ):
                raise ValueError("invalid forecast length")
            datetime.fromisoformat(current["time"])
            days = []
            for i in range(args.forecast_days):
                date.fromisoformat(daily["time"][i])
                days.append(
                    dict(
                        zip(
                            (
                                "date",
                                "weather_code",
                                "temperature_max",
                                "temperature_min",
                                "precipitation_probability",
                            ),
                            (daily[k][i] for k in keys),
                            strict=True,
                        )
                    )
                )
            result = WeatherResult.model_validate(
                {
                    "location": resolved.model_dump(),
                    "current": {
                        "time": current["time"],
                        "temperature": current["temperature_2m"],
                        "apparent_temperature": current["apparent_temperature"],
                        "relative_humidity": current["relative_humidity_2m"],
                        "weather_code": current["weather_code"],
                        "wind_speed": current["wind_speed_10m"],
                    },
                    "daily": days,
                    "units": {
                        "temperature": "°F" if units == "imperial" else "°C",
                        "wind_speed": "mp/h" if units == "imperial" else "km/h",
                    },
                    "source": SOURCE,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return result.model_dump()
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise SecurityError("weather_provider_error") from exc

    return Tool(
        "weather_tool",
        {
            **WeatherArguments.model_json_schema(),
            "description": (
                "Current weather and daily forecast from Open-Meteo. "
                "Results include required attribution."
            ),
        },
        ("weather.read",),
        lookup,
        input_model=WeatherArguments,
        output_model=WeatherResult,
    )

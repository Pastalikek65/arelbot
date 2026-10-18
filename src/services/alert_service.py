"""Pure numeric alert matching for typed public-data provider responses."""

from __future__ import annotations

from typing import Any


class AlertService:
    OPERATORS = {"above": ">=", "below": "<=", "gt": ">", "lt": "<"}

    @classmethod
    def matches(cls, data: Any, condition: dict[str, Any]) -> bool:
        if not isinstance(condition, dict) or condition.get("kind") != "threshold":
            return True
        try:
            target = float(condition["value"])
        except (TypeError, ValueError, KeyError):
            return False
        values = cls._values(data, str(condition.get("metric") or ""))
        operator = str(condition.get("operator") or "").casefold()
        for value in values:
            if operator in {"above", "gte", ">=", "üstü", "üzeri"} and value >= target:
                return True
            if operator in {"below", "lte", "<=", "altı", "altında"} and value <= target:
                return True
            if operator in {"gt", ">"} and value > target:
                return True
            if operator in {"lt", "<"} and value < target:
                return True
        return False

    @staticmethod
    def _values(data: Any, metric: str) -> list[float]:
        metric = metric.casefold().strip()
        if isinstance(data, dict) and isinstance(data.get("rates"), list):
            return [
                float(row["numeric_value"])
                for row in data["rates"]
                if isinstance(row, dict)
                and metric in str(row.get("name", "")).casefold()
                and isinstance(row.get("numeric_value"), (int, float))
            ]
        if isinstance(data, dict) and isinstance(data.get("prices"), list):
            values: list[float] = []
            for row in data["prices"]:
                if not isinstance(row, dict):
                    continue
                for key, value in row.items():
                    if metric in str(key).casefold() and isinstance(value, (int, float)):
                        values.append(float(value))
            return values
        if isinstance(data, dict) and isinstance(data.get("coins"), list):
            aliases = {
                "btc": "bitcoin", "eth": "ethereum", "sol": "solana", "xrp": "ripple",
                "doge": "dogecoin", "avax": "avalanche-2", "dot": "polkadot", "link": "chainlink",
            }
            target = aliases.get(metric, metric)
            values: list[float] = []
            for row in data["coins"]:
                if not isinstance(row, dict) or str(row.get("id", "")).casefold() != target:
                    continue
                key = "change_24h" if metric in {"change", "change_24h", "24h"} else "price"
                value = row.get(key)
                if isinstance(value, (int, float)):
                    values.append(float(value))
            return values
        return []

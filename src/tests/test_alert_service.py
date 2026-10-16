from src.services.alert_service import AlertService


def test_alert_service_matches_currency_thresholds():
    data = {"rates": [{"name": "USD", "numeric_value": 41.25}]}
    assert AlertService.matches(data, {"kind": "threshold", "metric": "usd", "operator": "above", "value": 40})
    assert not AlertService.matches(data, {"kind": "threshold", "metric": "usd", "operator": "below", "value": 40})


def test_alert_service_matches_fuel_metrics_and_rejects_unknown_values():
    data = {"prices": [{"city": "Ä°stanbul", "gasoline_value": 42.1, "diesel_value": 44.2}]}
    assert AlertService.matches(data, {"kind": "threshold", "metric": "gasoline", "operator": "below", "value": 45})
    assert not AlertService.matches(data, {"kind": "threshold", "metric": "lpg", "operator": "below", "value": 30})


def test_alert_service_matches_crypto_price_thresholds():
    data = {"coins": [{"id": "bitcoin", "price": 100500.0, "change_24h": 1.2}]}
    assert AlertService.matches(data, {"kind": "threshold", "metric": "btc", "operator": "above", "value": 100000})
    assert AlertService.matches(data, {"kind": "threshold", "metric": "bitcoin", "operator": "below", "value": 101000})

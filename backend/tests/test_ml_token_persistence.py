"""Mercado Libre single-use refresh tokens survive restarts."""

from flipradar.collectors import mercadolibre
from flipradar.db import session_scope
from flipradar.models import AppSetting
from flipradar.services.bootstrap import ML_TOKEN_KEY, install_ml_token_persistence


def test_rotated_token_is_persisted_and_restored(db, settings):
    seeded = settings.model_copy(update={"ml_refresh_token": "TG-seed"})
    install_ml_token_persistence(db, seeded)
    try:
        for listener in list(mercadolibre._REFRESH_LISTENERS):
            listener("TG-rotated-1")
        with session_scope() as s:
            assert s.get(AppSetting, ML_TOKEN_KEY).value_json["token"] == "TG-rotated-1"
        # "Restart": same seed configured -> the rotated token is used.
        restarted = settings.model_copy(update={"ml_refresh_token": "TG-seed"})
        install_ml_token_persistence(db, restarted)
        assert restarted.ml_refresh_token == "TG-rotated-1"
        # Operator re-authorised with a new token -> the new token wins.
        reauth = settings.model_copy(update={"ml_refresh_token": "TG-new"})
        install_ml_token_persistence(db, reauth)
        assert reauth.ml_refresh_token == "TG-new"
    finally:
        mercadolibre._REFRESH_LISTENERS.clear()

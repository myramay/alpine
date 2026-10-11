from agent import decide, WATER_GAL_PER_DAY_HIGH


def test_high_water_caps_towers():
    p, why = decide(WATER_GAL_PER_DAY_HIGH * 2, 0, 0, {"prefer_dry": False, "storage_reserve": 0.2})
    assert p["prefer_dry"] is True and p["tower_cap_mw"] == 20.0 and "water" in why


def test_quiet_day_no_change():
    p, why = decide(0, 0, 0, {"prefer_dry": True, "storage_reserve": 0.2})
    assert why == "no change" and p["storage_reserve"] == 0.2

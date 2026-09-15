from motopark_bot.geo import haversine_km, svy21_to_wgs84


def test_svy21_origin_roundtrips():
    lat, lon = svy21_to_wgs84(28001.642, 38744.572)
    assert abs(lat - 1.366666) < 1e-4
    assert abs(lon - 103.833333) < 1e-4


def test_svy21_known_carpark_lands_in_expected_area():
    # ACB = Albert Centre carpark, real record from data.gov.sg. Should land
    # near Bugis (~1.30, ~103.85), not somewhere absurd like Jurong or KL.
    lat, lon = svy21_to_wgs84(30314.7936, 31490.4942)
    assert 1.29 < lat < 1.31
    assert 103.84 < lon < 103.87


def test_haversine_zero_distance():
    assert haversine_km(1.3, 103.8, 1.3, 103.8) == 0.0


def test_haversine_known_distance_singapore_scale():
    # Roughly City Hall to Changi Airport is ~15-18km as the crow flies.
    city_hall = (1.2931, 103.8520)
    changi = (1.3644, 103.9915)
    d = haversine_km(*city_hall, *changi)
    assert 14.0 < d < 20.0


def test_haversine_is_symmetric():
    a = (1.30, 103.85)
    b = (1.35, 103.90)
    assert abs(haversine_km(*a, *b) - haversine_km(*b, *a)) < 1e-9

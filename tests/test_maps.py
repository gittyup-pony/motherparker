from motopark_bot.maps import maps_url


def test_maps_url_embeds_coordinates_in_google_maps_directions_format():
    url = maps_url(1.301059, 103.855409)
    assert url == "https://www.google.com/maps/dir/?api=1&destination=1.301059,103.855409"


def test_maps_url_handles_negative_coordinates():
    url = maps_url(-1.5, 103.8)
    assert "destination=-1.5,103.8" in url

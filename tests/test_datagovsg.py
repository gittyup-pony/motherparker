import json
from pathlib import Path

from motopark_bot.datagovsg import parse_csv_text, parse_geojson

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_geojson_clean_properties():
    data = json.loads((FIXTURES / "sample_ura_capacity_clean.geojson.json").read_text())
    features = parse_geojson(data)
    assert len(features) == 2

    f = features[0]
    assert f.lon == 103.8480
    assert f.lat == 1.3005
    assert f.properties["PP_CODE"] == "SP0001"
    assert f.properties["NO_MCYCLE"] == "40"


def test_parse_geojson_html_description_fallback():
    data = json.loads((FIXTURES / "sample_ura_capacity_html_description.geojson.json").read_text())
    features = parse_geojson(data)
    assert len(features) == 1

    f = features[0]
    # These only exist because the HTML-table-in-Description parser found
    # them - "Name" and "Description" are the only real top-level keys.
    assert f.properties["PP_CODE"] == "SP0001"
    assert f.properties["PARKING_PL"] == "ORCHARD ROAD CARPARK"
    assert f.properties["NO_MCYCLE"] == "40"


def test_parse_geojson_skips_non_point_geometry():
    data = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [103.8, 1.3]}, "properties": {"X": "1"}},
        ],
    }
    features = parse_geojson(data)
    assert len(features) == 1
    assert features[0].properties["X"] == "1"


def test_parse_geojson_empty_features():
    assert parse_geojson({"type": "FeatureCollection", "features": []}) == []
    assert parse_geojson({}) == []


def test_parse_csv_text_basic():
    text = (FIXTURES / "sample_carpark_rates.csv").read_text()
    rows = parse_csv_text(text)
    assert len(rows) == 3
    assert rows[0]["Carpark"] == "ION Orchard"
    assert rows[0]["Category"] == "Orchard"

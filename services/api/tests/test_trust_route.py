import httpx

from .test_api import hdr, packet, post, rec, register
from .test_pipeline import VARNA, _node, _run_worker

# 10 km west to east through the jammed area around Varna (lat 43.1989)
ROUTE = {
    "type": "LineString",
    "coordinates": [[27.85, 43.1989], [27.9166, 43.1989], [27.98, 43.1989]],
}


async def make_jam(c: httpx.AsyncClient) -> None:
    a, b = await _node(c, "a"), await _node(c, "b", "tbeam")
    await post(c, packet(rec(seq=1, confidence=30, lat_e7=VARNA[0], lon_e7=VARNA[1]), a), ext="a")
    await post(
        c,
        packet(rec(seq=1, confidence=30, lat_e7=VARNA[0] + 9000, lon_e7=VARNA[1] + 9000), b),
        ext="b",
    )
    await _run_worker(c)


async def route(c: httpx.AsyncClient, body: dict, role: str = "public", **params):  # type: ignore[no-untyped-def]
    return await c.post("/v1/trust/route", json=body, params=params, headers=hdr(c, role))


async def test_route_through_a_jammed_area_has_segments_and_a_worst_state(client):  # type: ignore[no-untyped-def]
    await make_jam(client)
    r = await route(client, ROUTE)
    assert r.status_code == 200
    d = r.json()
    states = [s["state"] for s in d["segments"]]
    assert "JAMMED" in states and "UNKNOWN" in states  # open ground at both ends has no evidence
    assert d["overall"]["state"] == "JAMMED" and d["overall"]["worst_segment"] == states.index(
        "JAMMED"
    )
    assert d["overall"]["score"] is not None and d["overall"]["independent_sources"] >= 1
    assert d["length_m"] > 9000 and d["sources"] == ["witness_nodes"] and d["sim"] is False
    assert d["data_age_s"] is not None and d["alternatives"] == []
    seg = d["segments"][0]
    assert seg["geometry"]["type"] == "LineString" and seg["start_m"] == 0
    assert d["segments"][-1]["end_m"] == d["length_m"]
    for s in d["segments"]:  # coarse, public-safe fields only
        assert set(s) == {
            "index",
            "start_m",
            "end_m",
            "geometry",
            "state",
            "score",
            "confidence",
            "data_age_s",
            "independent_sources",
            "sim",
        }


async def test_route_without_any_evidence_is_unknown_not_trusted(client):  # type: ignore[no-untyped-def]
    d = (await route(client, ROUTE)).json()
    assert [s["state"] for s in d["segments"]] == ["UNKNOWN"]
    assert (
        d["overall"]["score"] is None
        and d["overall"]["data_age_s"] is None
        and d["overall"]["confidence"] == 0
    )


async def test_route_next_to_trouble_needs_a_corridor(client):  # type: ignore[no-untyped-def]
    await make_jam(client)
    # Reports cover their own cell plus four rings (about 4 km). This line runs 5.5 km north of them.
    line = {"type": "LineString", "coordinates": [[27.90, 43.1989 + 0.05], [27.93, 43.1989 + 0.05]]}
    assert (await route(client, line)).json()["overall"]["state"] == "UNKNOWN"
    wide = (await route(client, line, buffer_m=2000)).json()
    assert (
        wide["overall"]["state"] in ("JAMMED", "DEGRADED") and wide["overall"]["score"] is not None
    )
    assert (await route(client, line, buffer_m=5000)).status_code == 422  # corridor limit is 2 km


async def test_simulated_evidence_marks_the_route(client):  # type: ignore[no-untyped-def]
    s = await register(client, kind="sim", ext="sim-1", transport="sim")
    await post(
        client,
        packet(rec(seq=1, confidence=30, lat_e7=VARNA[0], lon_e7=VARNA[1]), s),
        path="sim",
        ext="sim-1",
    )
    await _run_worker(client)
    d = (await route(client, ROUTE)).json()
    assert d["sim"] is True and any(seg["sim"] for seg in d["segments"])


async def test_route_input_is_validated_and_open_to_every_role(client):  # type: ignore[no-untyped-def]
    for bad in (
        {"type": "LineString", "coordinates": [[27.9, 43.2]]},  # one point
        {"type": "Point", "coordinates": [27.9, 43.2]},
        {
            "type": "LineString",
            "coordinates": [[27.9, 43.2], [200, 43.2]],
        },  # longitude out of range
        {"type": "LineString", "coordinates": [[27.9, 43.2]] * 501},  # too many positions
        {"type": "LineString", "coordinates": [[0, 0], [90, 0]]},  # far too long a route
    ):
        r = await route(client, bad)
        assert r.status_code == 422 and r.headers["content-type"].startswith(
            "application/problem+json"
        ), bad
    for role in ("public", "responder", "operator", "admin"):
        assert (await route(client, ROUTE, role=role)).status_code == 200
    assert (await client.post("/v1/trust/route", json=ROUTE)).status_code == 401

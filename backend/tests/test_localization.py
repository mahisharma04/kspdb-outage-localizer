"""Correctness tests for the localization algorithm.

Per the brief, this is where correctness lives: a known fault in a known
topology must produce the expected span. These tests build small hand-made
topologies and assert on the boundary, the grouping, simultaneous faults, the
DT / feeder / sensor classifications, the missing-device range, and the
confidence behaviour.
"""
from __future__ import annotations

from app.localization import DARK, LIVE, UNKNOWN, localize
from tests.helpers import GraphBuilder, line_dt


def _states(graph, dark=(), unknown=()):
    """All device poles LIVE except the given dark/unknown ones."""
    s = {}
    for pid, p in graph.poles.items():
        s[pid] = LIVE if p.has_device else UNKNOWN
    for pid in dark:
        s[pid] = DARK
    for pid in unknown:
        s[pid] = UNKNOWN
    return s


def build_branch_dt():
    """DT -> P1 -> P2 -> P3 -> P4  and  P2 -> P5 -> P6 (branch)."""
    b = GraphBuilder().feeder("F-1")
    b.dt("D-1", "F-1", households=120)
    coords = {
        "P1": (12.9701, 77.5901), "P2": (12.9704, 77.5902),
        "P3": (12.9707, 77.5903), "P4": (12.9710, 77.5904),
        "P5": (12.9705, 77.5910), "P6": (12.9706, 77.5915),
    }
    b.pole("P1", "D-1", None, *coords["P1"])
    b.pole("P2", "D-1", "P1", *coords["P2"])
    b.pole("P3", "D-1", "P2", *coords["P3"])
    b.pole("P4", "D-1", "P3", *coords["P4"])
    b.pole("P5", "D-1", "P2", *coords["P5"])
    b.pole("P6", "D-1", "P5", *coords["P6"])
    return b.build()


def test_single_span_fault_localizes_to_the_correct_edge():
    g = build_branch_dt()
    st = _states(g, dark=["P3", "P4"])
    incs = localize(g, st, corroborated={"P3"})
    assert len(incs) == 1
    inc = incs[0]
    assert inc.fault_type == "span"
    assert inc.span_from_pole == "P2" and inc.span_to_pole == "P3"
    assert inc.poles_affected == 2                 # P3 + P4
    assert inc.localization_kind == "span_point"
    assert inc.confidence_band == "high"           # recorded + corroborated


def test_many_dark_poles_group_into_one_incident():
    g = build_branch_dt()
    # A fault at the DT->P1 edge darkens the entire tree except nothing upstream.
    st = _states(g, dark=["P1", "P2", "P3", "P4", "P5", "P6"])
    incs = localize(g, st, corroborated={"P1"})
    # Every device pole dark and none live => DT-equipment fault, single incident.
    assert len(incs) == 1
    assert incs[0].fault_type == "dt"
    assert incs[0].localization_kind == "dt_equipment"


def test_partial_line_fault_is_a_single_span_not_dt():
    g = build_branch_dt()
    # P1 live, everything from P2 down dark (main + branch): one span at P1->P2.
    st = _states(g, dark=["P2", "P3", "P4", "P5", "P6"])
    incs = localize(g, st, corroborated={"P2"})
    assert len(incs) == 1
    assert incs[0].fault_type == "span"
    assert incs[0].span_from_pole == "P1" and incs[0].span_to_pole == "P2"
    assert incs[0].poles_affected == 5


def test_two_simultaneous_faults_produce_two_incidents():
    g = build_branch_dt()
    # Fault 1: P3->P4 boundary (P4 dark). Fault 2: P5->P6 boundary (P6 dark).
    st = _states(g, dark=["P4", "P6"])
    incs = localize(g, st, corroborated={"P4", "P6"})
    assert len(incs) == 2
    spans = {(i.span_from_pole, i.span_to_pole) for i in incs}
    assert ("P3", "P4") in spans and ("P5", "P6") in spans


def test_sensor_lie_dark_pole_with_live_children_is_not_an_outage():
    g = build_branch_dt()
    # P3 dark but P4 (its child) still live => impossible line fault.
    st = _states(g, dark=["P3"])
    st["P4"] = LIVE
    incs = localize(g, st)
    assert len(incs) == 1
    assert incs[0].fault_type == "sensor"
    assert incs[0].poles_affected == 1


def test_missing_device_on_boundary_reports_a_range():
    g = build_branch_dt()
    # P2 has no device (unknown). P3,P4 dark, P1 live.
    g.poles["P2"].has_device = False
    st = _states(g, dark=["P3", "P4"])
    st["P2"] = UNKNOWN
    incs = localize(g, st, corroborated={"P3"})
    assert len(incs) == 1
    inc = incs[0]
    assert inc.localization_kind == "span_range"
    # boundary walked up through unknown P2 to the live P1
    assert inc.span_from_pole == "P1" and inc.span_to_pole == "P3"


def test_low_quality_inferred_topology_degrades_to_dt_area():
    b = GraphBuilder().feeder("F-2")
    ids = line_dt(b, "D-2", "F-2", n=4, source="inferred", quality=0.30)
    g = b.build()
    st = _states(g, dark=[ids[2], ids[3]])   # last two dark
    incs = localize(g, st, corroborated={ids[2]})
    assert len(incs) == 1
    inc = incs[0]
    assert inc.localization_kind == "dt_area"
    assert inc.span_from_pole is None
    assert inc.topology_source == "inferred"


def test_inferred_scores_lower_than_recorded_under_identical_conditions():
    # Two identical straight lines, same fault, differing only in topology source.
    br = GraphBuilder().feeder("F-R")
    rids = line_dt(br, "D-R", "F-R", n=5, source="recorded", quality=1.0)
    gr = br.build()
    rec = localize(gr, _states(gr, dark=[rids[3], rids[4]]), corroborated={rids[3]})[0]

    bi = GraphBuilder().feeder("F-I")
    iids = line_dt(bi, "D-I", "F-I", n=5, source="inferred", quality=0.95)
    gi = bi.build()
    inf = localize(gi, _states(gi, dark=[iids[3], iids[4]]), corroborated={iids[3]})[0]

    assert inf.localization_kind in ("span_point", "span_range")
    assert inf.span_to_pole == iids[3]
    assert inf.confidence < rec.confidence      # inferred < recorded, same conditions


def test_feeder_fault_rolls_up_all_dts_into_one_incident():
    b = GraphBuilder().feeder("F-4")
    a = line_dt(b, "D-A", "F-4", n=3)
    c = line_dt(b, "D-C", "F-4", n=3)
    g = b.build()
    st = _states(g, dark=a + c)          # every pole on both DTs dark
    incs = localize(g, st, corroborated=set(a + c))
    assert len(incs) == 1
    assert incs[0].fault_type == "feeder"
    assert incs[0].feeder_id == "F-4"


def test_silence_without_power_lost_lowers_confidence():
    g = build_branch_dt()
    st = _states(g, dark=["P3", "P4"])
    with_corr = localize(g, st, corroborated={"P3"})[0].confidence
    without_corr = localize(g, st, corroborated=set())[0].confidence
    assert without_corr < with_corr

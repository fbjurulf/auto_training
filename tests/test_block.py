"""A1: blockdetektering ur timer-händelser och integritetskontrollen."""
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("TRANING_FIT_LOG", os.path.join(tempfile.gettempdir(), "traning-test-fit.log"))
os.environ.setdefault("TRANING_WEEKLY_LOG", os.path.join(tempfile.gettempdir(), "traning-test-weekly.log"))
os.environ.setdefault("TRANING_REPORT_DIR", os.path.join(tempfile.gettempdir(), "traning-test-rapporter"))

import blockdata
import parse_fit
from tests.fixtures import build_db

T0 = datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)


def _t(s):
    return T0 + timedelta(seconds=s)


def _timer(s, typ, event="timer"):
    return {"timestamp": _t(s), "event": event, "event_type": typ}


def _records(*spann):
    """Ett record per sekund, 3 m/s, inom varje (från, till)."""
    ut, dist = [], 0.0
    for fran, till in spann:
        for s in range(fran, till + 1):
            ut.append({"timestamp": _t(s), "distance": dist, "heart_rate": 140})
            dist += 3.0
    return ut


class TimerBlocksTests(unittest.TestCase):
    def test_last_block_closed_by_session_stop(self):
        # Som 2026-10-22: sista blocket stängs av session/stop_all, inte timer.
        events = [_timer(0, "start"), _timer(600, "stop_all"), _timer(615, "start"),
                  _timer(1800, "stop_all", event="session")]
        blocks = parse_fit.timer_blocks(events, _records((0, 600), (615, 1800)), _t(1800))
        self.assertEqual([round(b["duration_s"]) for b in blocks], [600, 1185])

    def test_open_block_closed_at_session_end(self):
        events = [_timer(0, "start"), _timer(600, "stop_all"), _timer(615, "start")]
        blocks = parse_fit.timer_blocks(events, _records((0, 600), (615, 1800)), _t(1800))
        self.assertEqual([round(b["duration_s"]) for b in blocks], [600, 1185])

    def test_pause_without_resume_ends_at_next_movement(self):
        events = [_timer(0, "start"), _timer(600, "stop_all"), _timer(1800, "stop_all")]
        blocks = parse_fit.timer_blocks(events, _records((0, 600), (640, 1800)), _t(1800))
        self.assertEqual([(round((b["start"] - T0).total_seconds()), round(b["duration_s"]))
                          for b in blocks], [(0, 600), (640, 1160)])

    def test_unpaused_run_has_no_blocks(self):
        events = [_timer(0, "start"), _timer(1800, "stop_all", event="session")]
        self.assertEqual(parse_fit.timer_blocks(events, _records((0, 1800)), _t(1800)), [])


def _seg(start_s, duration_s, distance_m):
    return {"started_at": _t(start_s).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_s": duration_s, "distance_m": distance_m}


class ReliabilityTests(unittest.TestCase):
    def test_blocks_covering_distance_and_time_are_reliable(self):
        block = [_seg(0, 600, 2000), _seg(615, 1185, 4000)]
        self.assertTrue(blockdata.palitliga(block, 6000, 1800))

    def test_missing_distance_is_unreliable(self):
        block = [_seg(0, 600, 2000), _seg(615, 300, 1000)]
        self.assertFalse(blockdata.palitliga(block, 6000, 1800))

    def test_missing_time_is_unreliable(self):
        # Distansen går ihop men blocken täcker bara halva den förflutna tiden.
        block = [_seg(0, 450, 3000), _seg(460, 440, 3000)]
        self.assertFalse(blockdata.palitliga(block, 6000, 1800))


class LapReferenceTests(unittest.TestCase):
    def test_distance_laps_use_active_time(self):
        # 2026-10-18: varvsumma 3960, aktiv 3965, förfluten 4593.
        self.assertEqual(blockdata.varv_referens(3960, 3965, 4593), ("aktiv", False))

    def test_manual_laps_use_elapsed_time(self):
        # 2026-10-22: varvsumma 4617, aktiv 4567, förfluten 4617.
        self.assertEqual(blockdata.varv_referens(4617, 4567, 4617), ("forfluten", False))

    def test_more_than_30_s_from_nearest_reference_warns(self):
        self.assertEqual(blockdata.varv_referens(4000, 3965, 4593), ("aktiv", True))

    def test_far_from_both_references_warns(self):
        # Närmast förfluten, men långt från båda - kontrollen får inte tystna.
        self.assertEqual(blockdata.varv_referens(5200, 3965, 4593), ("forfluten", True))

    def test_within_30_s_but_more_than_2_percent_from_both_warns(self):
        # Kort pass: 25 s från aktiv tid är under 30 s men över 2 % av båda.
        self.assertEqual(blockdata.varv_referens(625, 600, 660), ("aktiv", True))


class LapTailTests(unittest.TestCase):
    def _varv(self, *spann):
        return [{"started_at": _t(fran).strftime("%Y-%m-%dT%H:%M:%SZ"), "duration_s": tid}
                for fran, tid in spann]

    def test_tail_after_last_lap_is_footnote(self):
        # Som pass 94: varven tar slut 36 s före passet, inga luckor emellan.
        varv = self._varv((0, 1000), (1000, 1000), (2000, 564))
        ended = _t(2600).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(blockdata.varv_kontroll(varv, 2600, 2600, ended), ("aktiv", False, 36))

    def test_gap_inside_workout_warns(self):
        # Samma 36 s fattas, men mitt i passet - varvens siffror går inte att lita på.
        varv = self._varv((0, 1000), (1036, 1000), (2036, 564))
        ended = _t(2600).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(blockdata.varv_kontroll(varv, 2600, 2600, ended), ("aktiv", True, 0))

    def test_complete_laps_have_no_tail_note(self):
        varv = self._varv((0, 1300), (1300, 1298))
        ended = _t(2600).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(blockdata.varv_kontroll(varv, 2600, 2600, ended), ("aktiv", False, 0))


def _b(duration_s, distance_m, hr=140):
    return {"duration_s": duration_s, "distance_m": distance_m, "hr_avg": hr, "hr_max": hr}


class MergeTests(unittest.TestCase):
    def test_empty_block_merges_with_next(self):
        # 2026-10-22: 4 s utan distans mellan två riktiga block.
        ut = blockdata.sla_ihop([_b(1091, 3011), _b(4, None), _b(833, 2560), _b(2638, 7761)])
        self.assertEqual([(b["duration_s"], b["distance_m"], b["sammanslagen"]) for b in ut],
                         [(1091, 3011, False), (837, 2560, True), (2638, 7761, False)])

    def test_short_last_block_merges_with_previous(self):
        ut = blockdata.sla_ihop([_b(600, 2000), _b(900, 3000), _b(20, 60)])
        self.assertEqual([(b["duration_s"], b["distance_m"], b["sammanslagen"]) for b in ut],
                         [(600, 2000, False), (920, 3060, True)])

    def test_limits_are_50_m_and_30_s(self):
        self.assertEqual(len(blockdata.sla_ihop([_b(30, 50), _b(30, 50)])), 2)
        self.assertEqual(len(blockdata.sla_ihop([_b(29, 500), _b(30, 50)])), 1)
        self.assertEqual(len(blockdata.sla_ihop([_b(300, 49), _b(30, 50)])), 1)

    def test_heart_rate_weighted_by_time(self):
        ut = blockdata.sla_ihop([_b(10, 20, hr=100), _b(90, 300, hr=150)])
        self.assertEqual((ut[0]["hr_avg"], ut[0]["hr_max"]), (145, 150))


class ReportTests(unittest.TestCase):
    def _report(self, rows, distance_m=6000, elapsed_s=1800):
        import sqlite3
        import weekly
        conn = build_db()
        conn.row_factory = sqlite3.Row
        wid = conn.execute(
            """INSERT INTO workout (source_id, external_id, sport_id, started_at, ended_at,
                 local_date, duration_s, elapsed_s, distance_m)
               VALUES ((SELECT id FROM source WHERE code='healthfit_fit'), 'x',
                       (SELECT id FROM sport WHERE code='run'), ?, ?, '2026-10-22', ?, ?, ?)""",
            (_t(0).strftime("%Y-%m-%dT%H:%M:%SZ"), _t(elapsed_s).strftime("%Y-%m-%dT%H:%M:%SZ"),
             elapsed_s - 15, elapsed_s, distance_m)).lastrowid
        conn.execute("INSERT INTO workout_run (workout_id, pace_s_per_km) VALUES (?, 298)", (wid,))
        for seq, (kind, s) in enumerate(rows, 1):
            conn.execute(
                """INSERT INTO workout_segment (workout_id, seq, kind, started_at,
                     duration_s, distance_m) VALUES (?,?,?,?,?,?)""",
                (wid, seq, kind, s["started_at"], s["duration_s"], s["distance_m"]))
        return weekly.build_report(conn, date(2026, 10, 19), date(2026, 10, 25))

    def test_merged_block_gets_footnote(self):
        rapport = self._report([
            ("interval", _seg(0, 600, 2000)), ("interval", _seg(610, 4, None)),
            ("interval", _seg(620, 1180, 4000)),
            ("split", _seg(0, 900, 3000)), ("split", _seg(900, 900, 3000))])
        self.assertIn("| 2* | 4,00 km |", rapport)
        self.assertIn(blockdata.FOTNOT, rapport)

    def test_one_block_after_merge_shows_no_block_table(self):
        rapport = self._report([
            ("interval", _seg(0, 1780, 5990)), ("interval", _seg(1790, 10, 10)),
            ("split", _seg(0, 900, 3000)), ("split", _seg(900, 900, 3000))])
        self.assertNotIn("Block mellan pauser", rapport)
        self.assertIn("| Varv | Distans |", rapport)


    def test_unreliable_blocks_hide_table_but_keep_laps(self):
        rapport = self._report([
            ("interval", _seg(0, 600, 2000)), ("interval", _seg(615, 300, 1000)),
            ("split", _seg(0, 900, 3000)), ("split", _seg(900, 900, 3000))])
        self.assertIn(blockdata.OPALITLIG, rapport)
        self.assertNotIn("Block mellan pauser", rapport)
        self.assertIn("| Varv | Distans |", rapport)


if __name__ == "__main__":
    unittest.main()

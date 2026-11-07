"""Read-only, time-bounded fixture query. No networking or database writes."""
import argparse
import datetime as dt
import json
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "runtime/fixtures/mock_uid_00001_first_month.json"
TZ = dt.timezone(dt.timedelta(hours=8))
COURSE_POLICY = {
    "mode": "online_daily_unlock",
    "completion_requirement": "每日课程解锁后，当日读完即可，日内可自行安排学习时间。",
    "fixed_start_time": None,
    "intraday_deadline": None,
    "interpretation": "没有固定上课时段；实际开始、完课记录不是上课要求。当天未完成仍可继续学习，跨日未完成才计入待补读；历史提醒中的17点上课、18点截止不适用。",
}


def course_day(row):
    """Discard legacy fixture-only class hours, including in older scenarios."""
    value = {k: v for k, v in row.items() if k not in {'planned_start_at', 'due_at'}}
    value['complete_by_date'] = row['date'] if row.get('is_course_day', True) else None
    return value

def query(as_of, day=None, data=None):
    now = dt.datetime.fromisoformat(as_of)
    if now.tzinfo is None:
        raise ValueError("as_of must include timezone, e.g. +08:00")
    now = now.astimezone(TZ)
    at = now.isoformat(timespec="seconds")
    data = data if data is not None else json.loads(FILE.read_text(encoding="utf8"))
    if not data["window"]["start"] <= at[:10] <= data["window"]["end"]:
        raise ValueError("as_of is outside the first-month fixture")
    target = day or at[:10]
    timetable = next((r for r in data["course_calendar"] if r["date"] == target), None)
    if timetable is None:
        raise ValueError("requested date is outside the timetable")
    visible = lambda value: value is not None and value <= at
    lesson_rows = []
    for l in data["learning_daily"]:
        if l["date"] > at[:10]:
            continue
        completed = visible(l["completed_at"])
        attended = visible(l["attended_at"])
        # Calendar-day completion, not the old synthetic 18:00 cutoff. Comparing
        # local dates also handles the exact midnight boundary without a gap.
        overdue = l['date'] < at[:10] and not completed
        status = "completed" if completed else "in_progress" if attended else "absent" if overdue else "not_started"
        lesson_rows.append({
            "uid":data["uid"],"lesson_id":l["id"],"lesson_no":l["lesson_no"],
            "scheduled_date":l["date"],"title":l["title"],"status":status,
            "unlocked_at":l.get("unlocked_at",l["date"]+"T07:00:00+08:00"),
            "unlocked":visible(l.get("unlocked_at",l["date"]+"T07:00:00+08:00")),
            "complete_by_date":l['date'],"overdue":overdue,
            "attended_at":l["attended_at"] if attended else None,
            "completed_at":l["completed_at"] if completed else None,
            "checked_in_at":l["checked_in_at"] if visible(l["checked_in_at"]) else None,
        })
    expressions=[]
    for e in data["expression_assignments"]:
        if e["scheduled_date"]>at[:10]:
            continue
        record={k:e[k] for k in ["id","uid","lesson_id","lesson_no","scheduled_date","title","question","source"]}
        record.update({
            "answer":e["answer"] if visible(e["submitted_at"]) else None,
            "submitted_at":e["submitted_at"] if visible(e["submitted_at"]) else None,
            "selection_recorded_at":e["selection_recorded_at"] if visible(e["selection_recorded_at"]) else None,
        })
        expressions.append(record)
    return {
        "uid":data["uid"],"as_of":at,"profile":data["profile"],"enrollments":data["enrollments"],
        "course_policy":dict(COURSE_POLICY),
        "requested_day":course_day(timetable),"lessons_as_of":lesson_rows,
        "summary_as_of":{"scheduled":len(lesson_rows),"attended":sum(bool(l["attended_at"]) for l in lesson_rows),
                         "completed":sum(l["status"]=="completed" for l in lesson_rows),
                         "overdue":sum(l["overdue"] for l in lesson_rows)},
        "events":[e for e in data["learning_events"] if visible(e["occurred_at"])],
        "quiz_attempts":[q for q in data["quiz_attempts"] if visible(q["submitted_at"])],
        "expression_assignments":expressions,
        "rankings":[r for r in data["weekly_rankings"] if visible(r["published_at"])],
        "books":[{"id":b["id"],"title":b["title"],"lesson_ids":b["lesson_ids"],
                  "completed_at":b["completed_at"] if visible(b["completed_at"]) else None,
                  "shelf_lit_at":b["shelf_lit_at"] if visible(b["shelf_lit_at"]) else None} for b in data["books"]],
        "available_highlights":[h for h in data["highlight_assets"]
            if h["trigger_status"]=="eligible_fixture" and visible(h["candidate_at"]) and at<=h["expires_at"]],
        "source":"local_mock_fixture",
    }

if __name__=="__main__":
    p=argparse.ArgumentParser(description="Query UID 00001 at a simulated timestamp")
    p.add_argument("--as-of",required=True)
    p.add_argument("--day",help="Optional timetable date (no future outcome data returned)")
    p.add_argument("--highlight-id",help="Return only an available existing-image highlight")
    args=p.parse_args()
    result=query(args.as_of,args.day)
    if args.highlight_id:
        result=next((h for h in result["available_highlights"] if h["id"]==args.highlight_id),
                    {"uid":"00001","status":"not_available_at_as_of","delivered_at":None})
    print(json.dumps(result,ensure_ascii=False,indent=2))

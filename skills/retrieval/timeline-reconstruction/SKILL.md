---
name: timeline-reconstruction
description: Build a chronological timeline with explicit time precision.
---

Use date bounds, people, project or topic filters as appropriate.
Use `query.events` for known event labels and check `unresolved_events` before
drawing conclusions.
Ordering uses source timestamps or dates and then source offsets where available.
Use `query.time_axis: event` to filter and order by explicit event dates instead.
Report each `event_date_basis` as explicit, inferred or unknown. Unbounded event
timelines place inferred and unknown dates after explicitly dated evidence. Under
date bounds, report `omitted_inferred_time` and `omitted_unknown_time` separately.
Keep inferred dates tentative and flag conflicts with overlapping or unavailable times.

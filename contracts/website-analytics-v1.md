# Website analytics read contract v1

Project Desk reads one local report from
`runtime/analytics/<project_id>.json`. The file is runtime data and is never part
of the open-source export.

The report must identify GA4, the selected project, a hashed property identifier,
fetch time, reporting window and property timezone. It carries only three initial
metrics: active users, engaged sessions and download clicks. Each value is a
non-negative integer or `null` when unavailable. A verified `0` is therefore
different from missing data.

Reports fail closed when their identity, shape, dates, traffic-exclusion statement
or values are invalid. The Desk never receives the raw property identifier,
credentials or OAuth material. `pipeline/ga4_connector.py` now owns the bounded
read-only Data API request. It uses a short-lived OAuth bearer token supplied only
through `GROWTH_GA4_ACCESS_TOKEN`, makes separate overview and event-count queries,
then atomically replaces this local report. `runtime/ga4.json` is private and
gitignored; its public example starts disabled. The connector does not create a GA4
property, install tracking, verify the property's internal-traffic filter, or mint
and refresh OAuth credentials. Those remain explicit setup boundaries.

Native owner analytics use the sibling file
`runtime/analytics/<project_id>-platforms.json`. YouTube, Instagram, TikTok, X and
Pinterest each retain their own reporting window and named metrics. The read model
does not expose account identifiers and never combines or compares unlike metrics.

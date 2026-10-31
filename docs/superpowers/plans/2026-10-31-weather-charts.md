# Weather and Charts Implementation Plan

**Goal:** Add Executor-controlled weather lookup and locally rendered interactive charts/Mermaid diagrams to Lucid.

**Architecture:** `weather_tool` obtains current/daily forecast data from fixed Open-Meteo geocoding/forecast HTTPS endpoints. Dedicated `weather.read` capability defaults to deny and grants only those provider calls; general network/local-only defaults stay intact. `chart_tool` validates a bounded data specification and returns it. Host code captures only successful authorized chart_tool outputs and saves chart blocks beside the final text in existing JSONB reply content. No model code is executed and no schema migration is needed. Lucid validates chart blocks and renders them locally with tooltips/series toggles; Mermaid uses strict mode plus sanitized SVG and bounded source.

**Contract:** chart block `{type:"chart",spec:ChartSpec}`. ChartSpec keys `kind` (line/bar/scatter/pie/mermaid), `title` (<=120), `x_label`/`y_label` (<=80), `labels` (<=200 strings each<=80), `series` (<=6 objects with `name`<=80 and `values`<=200 finite numbers), `mermaid` (<=12000). All keys default empty except required kind; numeric charts require nonempty matching labels/values, pie one nonnegative series, scatter numeric x labels. Mermaid requires nonempty safe bounded source and no series/labels. Numeric charts disallow Mermaid source. Up to four chart blocks after the first text block; user input stays text-only. Existing rows/variants remain compatible. Streaming emits chart blocks only on committed completion.

**Weather:** location string<=100, country_code optional empty/two letters, forecast_days1..7 default3, units imperial/metric defaultimperial. No arbitrary URLs, API keys, headers, shell, eval or browser geolocation. Forecast result includes resolved location/coordinates/timezone, current conditions, dated daily forecast, units/source/retrieval time. Reject private/special DNS responses for the public-provider exception, pin addresses and disallow redirects. Source attribution required.

## Tasks
- [x] Weather worker: failing deny/SSRF/schema tests, strict models, bounded provider handler and isolated capability; report actual tests.
- [x] Renderer worker: ChartSpec validator, safe Mermaid renderer and interactive SVG charts, focused tests; no browser verification.
- [x] Coordinator: chart models/tool, registry/server integration, successful-result capture, message/variant storage and frontend response parsing; security regressions first.
- [x] Full actual PostgreSQL/LangGraph suite, Ruff/format, dependency audits; frontend unit/type/build checks. Real weather and local model/tool smoke; no browser verification.
- [x] Peer review fixes, documentation, feature-branch commit/push to existing PR; required CI must pass, no merge.


## Validation notes

- Weather permission, DNS scope and provider-unit regressions failed before implementation/fixes; actual public weather call passed without credentials.
- Actual installed local model used weather, bar chart and Mermaid tools; successful calls were audited and chart blocks persisted in the test database. Synthetic fixture conversations were removed.
- Saved/re-generated/selected variants and streamed completion carry chart blocks; only the host success observer attaches artifacts. Text remains first for existing search/memory paths.
- Peer review found escaped Mermaid node metadata could trigger image fetching before SVG sanitation. Both validators now reject all node metadata, with regressions observed red then green and a clean follow-up review.
- Cold API import exposed a weather/model import cycle that collection order hid; a subprocess regression failed, then passed after deferring the strict-JSON import to request processing.
- Frontend bundle/type/unit checks and both dependency audits pass. Visual verification is intentionally left to the user.

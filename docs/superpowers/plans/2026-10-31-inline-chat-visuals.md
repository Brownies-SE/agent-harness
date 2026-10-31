# Inline Chat Visuals Implementation Plan

**Goal:** Charts and diagrams flow inside an assistant reply in meaningful order, including while streaming, rather than living in a separate end-of-answer card.

**Architecture:** Keep the model final JSON string contract. chart_tool returns a host-generated chart_id and matching literal reference alongside its existing normalized spec fields. The model can position that authorized current-run chart using [[chart:<chart_id>]] in final text. A bounded host-owned assembler replaces references with typed chart blocks, buffers split marker fragments, and emits text/chart events in order. Unknown/cross-run/duplicate/malformed references fail closed; unreferenced successful charts retain the legacy append fallback. No tool or resource restriction changes.

**Storage/API:** Assistant content becomes ordered TextBlock/ChartBlock, up to nine blocks (four charts/five text), including chart-first/chart-only replies. User content remains one text block. Existing text-first/then-charts history remains valid. The new SSE event is {type:"chart",chart:{type:"chart",spec:ChartSpec}}, alongside existing delta/complete/error. These are provisional display blocks until committed completion; cancellation/error reloads saved history. Normal JSON and saved variants retain the same ordered content. Text extraction joins every text block for context, search, memory and compaction. No migration.

**Frontend:** parseMessage preserves ordered blocks per answer variant, while content remains concatenated plain text for copying/search previews. Streaming appends/coalesces text blocks and appends validated chart events at their actual position. Render each block in a single response flow; pace only the current trailing text block and flush earlier text when a chart arrives. Existing typed chart/Mermaid safety remains. Remove outer chart card border/background/padding, keep useful title/legend/controls. User handles visual checks; no browser verification.

## Tasks
- [x] Add failing reference/permission/order/chunk-boundary regressions before changing backend paths.
- [x] Implement host chart IDs + assembler + typed chart SSE callback and ordered persistence/variants.
- [x] Update all text consumers (search/context/memory/compaction) to include text after charts.
- [x] Frontend worker: ordered parser, stream reducer and inline rendering/card removal; unit/type/build checks without browser.
- [x] Full PostgreSQL/LangGraph suite, Ruff/format, audits, live local-model streamed example, peer review fixes.
- [x] Restart local services, update docs, commit feature branch/push existing PR; wait required CI; no merge.


## Verification notes

- Reference generation, invalid/current-run boundaries, split markers, chart-first/chart-only placement and ordered saving were observed failing before implementation.
- Search and memory tests demonstrated missing text after charts and a chart-first indexing error before consumers were updated.
- Frontend ordered-parser/stream/reducer regressions were observed failing; the final UI keeps blocks immutable and plain text paragraphs separated.
- Fresh code review found no actionable blockers; browser and visual verification remain with the user.

- Actual local model produced streamed text/chart/text for an explicit placement fixture; the saved blocks matched and no marker fragments were displayed. More general prompts may still choose an end position; placement is now supported rather than structurally forced to the end.
- The host formatting hint is enabled only for its registered chart_tool with the exact trusted ChartResult output type. A follow-up review found no blockers.

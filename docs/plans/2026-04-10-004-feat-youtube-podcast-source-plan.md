---
title: "feat: YouTube podcast source with transcript-first discovery"
type: feat
status: active
date: 2026-04-10
---

# feat: YouTube podcast source with transcript-first discovery

## Overview

Add a "podcasts" source to last30days that discovers podcast content on YouTube by scanning transcripts, not searching titles. The LLM planner resolves topic-relevant podcast channels (e.g., "NVIDIA" -> Acquired, Lex Fridman, Dwarkesh Patel, All-In). The engine fetches recent episodes from those channels, downloads their auto-captions (no video download), and greps for the search topic. Episodes with 5+ topic mentions become podcast results with transcript highlights.

This finds content invisible to any search engine. Acquired's "The NFL" episode mentions Taylor Swift 18 times, ESPN 117 times, Netflix 102 times - none in the title. A Dwarkesh Patel episode titled "The single biggest bottleneck to scaling AI compute" contains 156 mentions of NVIDIA. No YouTube search finds these. Transcript scanning does.

Zero new API keys. Zero new dependencies. Reuses existing yt-dlp + transcript pipeline. Podcasts get their own identity in stats and synthesis.

## Problem Frame

YouTube captures a lot of podcast content, but it's mixed with news clips, reaction videos, and shorts. The general YouTube search treats a 2:24:55 Drink Champs interview the same as a 0:30 TMZ clip. Worse, the highest-value podcast content is often invisible to search entirely because the topic is discussed within an episode titled something else.

Two insights make this solvable:
1. Podcast episodes are identifiable by duration (>20 minutes) and channel.
2. YouTube auto-captions are free, downloadable without the video (~7 seconds per episode via yt-dlp), and searchable. Transcript scanning discovers content that title-based search cannot.

The LLM already resolves subreddits and X handles per topic. Podcast channels are the same pattern.

## Requirements Trace

- R1. LLM resolves topic-relevant podcast YouTube channels dynamically (no hardcoded list)
- R2. Engine scans recent episode transcripts for the search topic, not just titles
- R3. Podcast results get their own source identity with own stats line and synthesis treatment
- R4. Reuses existing yt-dlp transcript pipeline (no new dependencies)
- R5. Does not duplicate regular YouTube results (dedup by video ID in fusion)
- R6. Channel resolution works in both the agent layer (SKILL.md) and the Python planner

## Scope Boundaries

- Not building a new API integration (reuses yt-dlp entirely)
- Not adding PodcastIndex, AssemblyAI, or any podcast-specific API
- Not changing how the regular YouTube source works
- Not building a podcast channel database
- Channels that can't be resolved are skipped silently (graceful degradation)

## Context & Research

### Relevant Code and Patterns

- `scripts/lib/youtube_yt.py` - YouTube search + transcript pipeline. Key functions: `search_youtube()`, `fetch_transcripts()`, `extract_transcript_highlights()`
- `scripts/lib/youtube_yt.py` - `--write-auto-sub --skip-download` fetches captions without downloading video
- Step 0.55 in `SKILL.md` - subreddit resolution pattern (WebSearch + LLM knowledge -> `--subreddits=`)
- `scripts/lib/pipeline.py` - source dispatch via if/elif chain in `_retrieve_stream()`, 4-point registration pattern
- `scripts/lib/normalize.py` - `_normalize_youtube()` handles transcript data, reusable for podcasts
- `scripts/lib/signals.py` - `SOURCE_QUALITY` dict (YouTube is 0.85)
- `scripts/lib/planner.py` - `QueryPlan` schema, `SOURCE_CAPABILITIES` dict

### Proof of Concept Results (2026-04-10)

**Transcript-first discovery test:** Fetched auto-captions for 5 recent Acquired episodes (35 seconds total, no video download). Grepped for topics not in any episode title:

| Topic | Mentions | Episode title | Discoverable by search? |
|-------|----------|---------------|------------------------|
| ESPN | 117 | The NFL | No |
| Super Bowl | 108 | The NFL | No |
| Netflix | 102 | The NFL | No |
| Amazon | 87 | The NFL | No |
| Costco | 63 | The NFL / others | No |
| Disney | 48 | The NFL | No |
| LVMH | 27 | Formula 1 / others | No |
| Taylor Swift | 18 | The NFL | No |

**Full E2E test (topic: NVIDIA, 4 channels):** LLM resolved Acquired, Lex Fridman, Dwarkesh Patel, All-In. Scanned 14 episodes. Results:

| Podcast | Episode | NVIDIA mentions | Title mentions NVIDIA? |
|---------|---------|----------------|----------------------|
| Lex Fridman | Jensen Huang interview | 159 | Yes |
| Dwarkesh Patel | Dylan Patel: AI compute bottleneck | 156 | No |
| Acquired | 10 Years (w/ Michael Lewis) | 24 | No |
| All-In | SpaceX IPO, Iran, Quantum... | 6 | No |

3 of 4 hits are invisible to YouTube search. The Dylan Patel episode (156 mentions!) is entirely about NVIDIA's GPU supply chain but the title never says "NVIDIA."

**Channel handle resolution test:** LLM resolves podcast name + @handle guess. Engine tries @handle first (fast), falls back to `ytsearch1:` if wrong. Tested across 12 channels (tech, hip-hop, knitting): 11/12 resolved on first @handle attempt, 12/12 with fallback. Even niche channels (Fruity Knitting, Grocery Girls Knit, Roxanne Richardson) resolved correctly.

**Rate limit test:** 4 channels x 3-4 episodes = 14 caption fetches took ~2 minutes sequential. Parallelized with 4 workers: ~30-40 seconds. No YouTube throttling observed. Runs concurrently with Reddit/X/everything else in a 3-minute research run.

## Key Technical Decisions

- **Transcript-first discovery, not title/search-based:** The core innovation. Instead of searching YouTube for `{topic} {podcast_name}` (which only finds episodes titled about the topic), we fetch captions from recent episodes and grep for the topic. This discovers hidden mentions. The approach is validated by POC data showing 3/4 NVIDIA hits were invisible to search.

- **LLM-resolved channels, not hardcoded:** The LLM planner (agent layer or Python Gemini/OpenAI) resolves 6-12 channels per topic using two-dimensional reasoning: (1) domain podcasts that focus on the topic's area, (2) cross-domain podcasts that might cover it. Tested: the LLM correctly resolved channels for NVIDIA (tech), Kanye (hip-hop), and knitting (craft) - including niche channels like Fruity Knitting and Grocery Girls Knit. Three resolution paths mirror the existing planner architecture:
  - Path 1: Agent layer (SKILL.md with WebSearch) resolves channels in Step 0.55
  - Path 2: Python planner (Gemini/OpenAI) generates channels as a `podcast_channels` field in the QueryPlan
  - Path 3: Fallback (no LLM) uses a small default list of ~5 broad-appeal channels

- **Handle-first channel resolution with search fallback:** The LLM returns both the podcast name and its best guess at the @handle. The engine tries the @handle first (instant, 92% success rate in testing). If the handle fails, it falls back to `ytsearch1:"{podcast name}" podcast full episode` to find the channel URL. Channels that can't be resolved either way are skipped silently.

- **New source module wrapping YouTube functions:** `podcast_yt.py` imports `fetch_transcripts()` and `extract_transcript_highlights()` from `youtube_yt.py`. It adds the channel-fetching, caption-scanning, and mention-counting logic. This keeps the regular YouTube source untouched and gives podcasts their own pipeline identity.

- **Duration filter >= 1200 seconds (20 minutes):** Eliminates clips, shorts, and news segments. Tested empirically - only full podcast episodes survive this filter.

- **SOURCE_QUALITY: 0.88 (above YouTube's 0.85):** Podcast episodes contain long-form expert discussion with full context. The quality bonus ensures podcast results rank above equivalent YouTube clips when both exist.

- **Mention count threshold: 5+:** Episodes with fewer than 5 topic mentions are noise (passing references). 5+ indicates substantive discussion. Tested: Taylor Swift at 18 mentions in the NFL episode is substantive discussion of her impact on viewership. "Apple" at 3 mentions in a random episode is just name-dropping.

## Open Questions

### Resolved During Planning

- **Can yt-dlp fetch captions without downloading video?** Yes. `yt-dlp --write-auto-sub --sub-lang en --skip-download --sub-format vtt` fetches only the subtitle file. ~7 seconds per episode, ~2MB per 4-hour episode.
- **Will this double-count YouTube content?** No. Fusion deduplicates by item ID. Both sources use `yt_{video_id}` format.
- **Can LLMs resolve niche podcast channels?** Yes. Tested with knitting: Fruity Knitting, VeryPink Knits, Grocery Girls Knit, Roxanne Richardson all resolved correctly via @handle.
- **What about rate limits?** 14 caption fetches across 4 channels showed no throttling. Running in parallel with 4 workers keeps total time under 40 seconds. yt-dlp doesn't use the YouTube Data API (no quota).
- **How does the LLM know which podcasts to pick?** Two-dimensional prompt: (1) "What YouTube podcasts focus on {topic's domain}?" and (2) "What popular interview/deep-dive podcasts have likely discussed {topic}?" The LLM returns channel names + @handle guesses.

### Deferred to Implementation

- **Exact duration threshold:** Starting with 1200s (20 min). May tune to 900s (15 min) if testing shows missed content.
- **Mention count threshold tuning:** Starting with 5. May need per-source calibration (a 30-minute podcast with 5 mentions is denser than a 4-hour one with 5 mentions).
- **Caption language handling:** Starting with English (`--sub-lang en`). Multilingual support deferred.
- **Parallel worker count:** Starting with 4 workers. May tune based on YouTube throttling behavior at scale.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification.*

```
PODCAST DISCOVERY FLOW:

  User query: "NVIDIA"
       |
  LLM planner resolves podcast channels:
  "NVIDIA is a tech/AI company. Domain podcasts: none specific.
   Cross-domain: Acquired (@AcquiredFM), Lex Fridman (@lexfridman),
   Dwarkesh Patel (@DwarkeshPatel), All-In (@AllInPod)"
       |
  Engine receives: --podcast-channels=AcquiredFM,lexfridman,DwarkeshPatel,AllInPod
       |
  For each channel (parallel, 4 workers):
    |
    [1] Resolve @handle -> channel URL
        Try: https://youtube.com/@AcquiredFM/videos
        If fail: ytsearch1:"Acquired podcast full episode" -> extract channel_url
        If fail: skip channel
    |
    [2] Fetch last 3 episode IDs + metadata (duration, date, title)
        yt-dlp --flat-playlist --playlist-end 3
    |
    [3] Filter: duration >= 1200s AND upload_date in date range
    |
    [4] For each surviving episode:
        Fetch auto-captions: yt-dlp --write-auto-sub --skip-download
        Grep captions for "nvidia" (case-insensitive)
        If mentions >= 5: HIT - extract transcript highlights around mentions
    |
  Merge all hits, deduplicate by video_id
  Score: mention_count * log(views)
  Return as source="podcasts" items with transcript_snippet + mention_count
```

## Implementation Units

- [ ] **Unit 1: Podcast transcript-scan module**

**Goal:** Create `scripts/lib/podcast_yt.py` with the channel-fetching, caption-scanning, mention-counting pipeline. Returns podcast episodes discovered via transcript scanning.

**Requirements:** R2, R3, R4

**Dependencies:** None (youtube_yt.py already exists)

**Files:**
- Create: `scripts/lib/podcast_yt.py`
- Test: `tests/test_podcast_yt.py`

**Approach:**
- `search_podcast_youtube(topic, from_date, to_date, depth, channels)`:
  - For each channel handle (in parallel via ThreadPoolExecutor, max 4 workers):
    1. Resolve handle to channel URL (try @handle first, search fallback)
    2. Fetch last N episode IDs + metadata via `yt-dlp --flat-playlist --playlist-end N`
    3. Filter: `duration >= 1200` and `upload_date` within date range
    4. Fetch auto-captions via `yt-dlp --write-auto-sub --skip-download --sub-lang en`
    5. Grep captions for topic keywords (case-insensitive). Count mentions.
    6. If mentions >= MENTION_THRESHOLD: include as hit. Extract transcript highlights around mentions using `extract_transcript_highlights()` from `youtube_yt`.
  - Merge results, deduplicate by video_id
  - Score: `mention_count * log(views + 1)`
  - Skip channels that can't be resolved or have no recent episodes

- `resolve_channel(handle)`: Try `@{handle}` URL first. If 404, search `ytsearch1:"{handle}" podcast full episode`, extract channel_url. Return channel_url or None.

- EPISODES_PER_CHANNEL: quick=2, default=3, deep=4
- MENTION_THRESHOLD: 5
- RESULTS_CAP: quick=4, default=8, deep=20

**Patterns to follow:**
- `scripts/lib/youtube_yt.py` `search_and_transcribe()` for search-then-enrich flow
- `scripts/lib/youtube_yt.py` `extract_transcript_highlights()` for highlight extraction
- `scripts/lib/hackernews.py` for clean module structure with `_log()`, `DEPTH_CONFIG`

**Test scenarios:**
- Happy path (hidden mention): topic "Taylor Swift", channels=["AcquiredFM"] -> scans NFL episode, finds 18 mentions, returns episode with highlights about Taylor Swift's NFL viewership impact
- Happy path (title match): topic "kanye west", channels=["RevoltTV"] -> scans Kanye interview, finds 500+ mentions, returns with highlights
- Happy path (scoring): episode with 156 mentions and 205K views scores higher than one with 6 mentions and 145K views
- Happy path (handle resolution): @AcquiredFM resolves directly. @SomeWrongHandle fails, search fallback finds correct channel.
- Edge case: topic "quantum computing" has <5 mentions in all episodes -> returns empty (threshold not met)
- Edge case: @handle doesn't exist AND search fallback fails -> channel skipped silently, other channels still scanned
- Edge case: channel has no episodes in date range -> skipped
- Edge case: episode has no auto-captions available -> skipped with log warning
- Error path: yt-dlp not installed -> returns empty items with log warning
- Error path: caption download times out -> skip that episode, continue

**Verification:**
- Discovers episodes where topic is discussed but not in the title (Acquired/NFL/Taylor Swift)
- Also discovers episodes where topic IS the subject (via same transcript scan)
- All returned items have duration >= 1200
- Each item has: video_id, title, channel, url, date, duration, engagement, transcript_snippet, mention_count

---

- [ ] **Unit 2: Pipeline integration**

**Goal:** Register "podcasts" as a new source in pipeline, normalizer, signals, planner, env, and render.

**Requirements:** R3, R5, R6

**Dependencies:** Unit 1

**Files:**
- Modify: `scripts/lib/pipeline.py` (import, MOCK_AVAILABLE_SOURCES, available_sources, _retrieve_stream)
- Modify: `scripts/lib/normalize.py` (add normalizer - reuse `_normalize_youtube` with source override)
- Modify: `scripts/lib/signals.py` (add SOURCE_QUALITY: 0.88)
- Modify: `scripts/lib/planner.py` (add SOURCE_CAPABILITIES, extend QueryPlan schema with `podcast_channels` field, add prompt guidance for LLM channel resolution)
- Modify: `scripts/lib/env.py` (add is_podcast_yt_available - checks yt-dlp installed + "podcasts" in INCLUDE_SOURCES)
- Modify: `scripts/lib/render.py` (add SOURCE_LABELS: "podcasts" -> "Podcasts")
- Test: `tests/test_podcast_yt.py` (pipeline dispatch test)

**Approach:**
- Availability: yt-dlp installed + "podcasts" in INCLUDE_SOURCES. No API key needed.
- SOURCE_CAPABILITIES: `{"podcasts": {"discussion", "longform", "expert", "interview"}}`
- Normalizer: reuse `_normalize_youtube` via lambda wrapper, override source to "podcasts". Add `mention_count` to metadata.
- CLI flag: `--podcast-channels=handle1,handle2,...` parsed from args
- Planner: extend QueryPlan with `podcast_channels: list[str]`. Prompt guidance for LLM: "List 6-12 YouTube podcast channel @handles that would discuss this topic. Think in two dimensions: (1) domain podcasts that focus on this area, (2) popular cross-domain interview/deep-dive podcasts that might cover it. Return @handles. If unsure of exact handle, return your best guess."
- Planner: include "podcasts" source for general/opinion/comparison intents
- Dedup: podcast items use `yt_{video_id}` ID format (same as YouTube). Fusion dedup handles collisions.

**Patterns to follow:**
- 4-point pipeline registration (same as all sources)
- `_normalize_youtube` reuse via lambda (like tiktok/instagram share `_normalize_shortform_video`)
- `scripts/lib/env.py` INCLUDE_SOURCES opt-in pattern

**Test scenarios:**
- Happy path: "podcasts" in available_sources when yt-dlp installed + INCLUDE_SOURCES contains "podcasts"
- Happy path: pipeline dispatches to podcast_yt.search_podcast_youtube when source="podcasts"
- Edge case: yt-dlp not installed -> podcasts not available
- Edge case: "podcasts" not in INCLUDE_SOURCES -> not available even with yt-dlp
- Integration: podcast video_id collides with YouTube result -> fusion deduplicates, keeps higher score

**Verification:**
- `python3 scripts/last30days.py "NVIDIA" --podcast-channels=AcquiredFM,lexfridman` returns podcast results
- Stats output shows "Podcasts" line separate from "YouTube"

---

- [ ] **Unit 3: SKILL.md podcast channel resolution + synthesis**

**Goal:** Add podcast channel resolution to Step 0.55 and podcast-specific synthesis guidance to the Judge Agent section.

**Requirements:** R1, R3, R6

**Dependencies:** Unit 2

**Files:**
- Modify: `SKILL.md`

**Approach:**
- **Step 0.55 addition:** Add "Resolve podcast channels" alongside subreddit, X handle, and TikTok resolution. The agent resolves 6-12 @handles using two-dimensional reasoning (domain + cross-domain). For niche topics, supplement with `WebSearch("{TOPIC} podcast YouTube channel")`. Display resolved channels: "Podcasts: @AcquiredFM, @lexfridman, @DrinkChamps". Pass as `--podcast-channels=AcquiredFM,lexfridman,DrinkChamps`.

- **Step 0.75 addition:** Add "podcasts" to available sources list. Include in primary subquery sources.

- **Synthesis guidance addition:** "For podcasts: lead with the guest's name and the podcast name. Quote transcript highlights as direct quotes with speaker attribution. Podcast content represents considered opinion, not hot takes - a 2-hour interview has more nuance than a tweet. When both a podcast and a YouTube clip cover the same topic, prefer the podcast's longer-form analysis."

- **Stats format:** `├─ 🎙️ Podcasts: {N} episodes │ {N} views │ {N} with transcripts`

- **INCLUDE_SOURCES:** Add "podcasts" as an option. Note in setup: "Requires yt-dlp (already installed if YouTube works). No API key needed."

- **Invitation section:** Reference podcast episodes in follow-up suggestions ("Want me to pull more from that Lex Fridman episode?")

**Patterns to follow:**
- Step 0.55 subreddit resolution pattern
- Source-specific synthesis guidance (YouTube highlights, Reddit top comments)

**Test scenarios:**
- Test expectation: none - SKILL.md is an instruction document. Verification is manual E2E.

**Verification:**
- `/last30days NVIDIA` resolves tech podcast channels and passes them to engine
- `/last30days Kanye West` resolves hip-hop podcast channels
- `/last30days knitting` resolves craft podcast channels (Fruity Knitting, etc.)
- Stats show 🎙️ Podcasts line. Synthesis quotes podcast content with speaker attribution.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| LLM guesses wrong @handle | Handle-first resolution with search fallback. 92% first-attempt success in testing, 100% with fallback. Wrong handles fail fast and skip silently. |
| Transcript scanning adds latency | Runs in parallel with all other sources. 4 channels x 3 episodes = ~30-40s parallelized. Invisible in a 3-minute research run. |
| Topic mentions below threshold (lots of misses) | LLM picks channels likely to discuss the topic. When it picks well, hit rate is high (4/14 episodes in NVIDIA test). Misses cost ~7s per episode in wasted caption download - acceptable. |
| YouTube throttles caption downloads | 14 sequential downloads showed no throttling. Capping at 4 parallel workers adds safety margin. If throttled, degrade gracefully (fewer episodes scanned). |
| Niche topics have no relevant podcast channels | LLM returns fewer channels (3-4 instead of 10-12). If none can be resolved, podcast source returns empty. Other sources (Reddit, X, YouTube) still run. |
| Same video in both YouTube and podcast results | Fusion deduplicates by `yt_{video_id}`. Podcast version gets 0.88 quality score vs YouTube's 0.85, so podcast version wins dedup. |

## Sources & References

- POC: transcript scan of 5 Acquired episodes found ESPN (117), Netflix (102), Taylor Swift (18), LVMH (27) - all invisible to search
- POC: E2E NVIDIA test across 4 channels found 5 hits, 3 invisible to search (including 156-mention Dwarkesh Patel episode)
- POC: handle resolution tested 12 channels (tech, hip-hop, knitting) - 11/12 first-attempt, 12/12 with fallback
- Related code: `scripts/lib/youtube_yt.py`, `scripts/lib/pipeline.py`, `scripts/lib/hackernews.py`
- Pattern: SKILL.md Step 0.55 subreddit resolution
- yt-dlp docs: https://github.com/yt-dlp/yt-dlp
- Acquired FM: https://www.youtube.com/@AcquiredFM

# CubingNow

CubingNow presents newly detected official speedcubing records and highlights notable competitors attending upcoming competitions.

## Language

**Record**:
A world, continental, or national Achievement assigned by CubingNow to a Canonical Result.
_Avoid_: Source claim, provider record

**Record Level**:
The geographic scope of a Record Achievement: world (WR), continental (CR), or national (NR). One Canonical Result may hold multiple Record Levels without being copied.
_Avoid_: Record type

**Achievement**:
A WR, CR, NR, or PR classification associated with one Canonical Result. An Achievement says what a result qualifies as, not whether CubingNow publishes or notifies it.
_Avoid_: Result copy, provider label

**Canonical Result**:
One finalized round-level best single or official average to which claims from one or more providers refer. Corrections revise this identity rather than creating another result.
_Avoid_: Source result, record row

**Provider Result State**:
The latest provider-specific state of one competitor's result in a round, whether unfinished or finalized. It retains every entered attempt and may change as the round progresses.
_Avoid_: Canonical Result, Achievement

**Finalized Result Observation**:
A provider's current claim about the finalized best single or official average for one competitor in one round. It never represents an individual attempt or unfinished result.
_Avoid_: Source Observation, Provider Result State

**Round Finalization**:
The point at which a competitor can receive no more attempts in a round, either because all expected attempts are entered or because the competitor failed a cutoff.
_Avoid_: Round finished, positive average

**Source Claim**:
A provider's assertion that an observed result has a particular record label. A Source Claim is evidence and is not CubingNow's classification.
_Avoid_: Achievement, verified record

**Qualification Decision**:
CubingNow's decision about whether an Achievement appears publicly or is eligible for notification. It is distinct from the Achievement itself.
_Avoid_: Classification, valid flag

**Record Validation**:
Independent, level-specific evidence that a Canonical Result meets the corresponding official WCA record benchmark. Record Validation confirms record qualification; it does not authenticate the underlying solve.
_Avoid_: Result verification, trusted source label

**WCA Record Snapshot**:
An auditable normalized capture of the official WCA records endpoint used for Record Validation. It is separate from the historical baseline used to replay effective live record state.
_Avoid_: Live benchmark, source claim

**Entry Time**:
The time a provider says a result was entered into the live competition system, when supplied.
_Avoid_: Observation time, solve time

**Observation Time**:
The time CubingNow first receives particular source evidence. It may be later than Entry Time.
_Avoid_: Entry time, record time

**Featured Competitor**:
A competitor included in CubingNow's manually curated set, identified by WCA ID, whose attendance at upcoming competitions is highlighted.
_Avoid_: Celebrity cuber, elite cuber

**Attendance Window**:
The inclusive Wednesday-through-Tuesday local-date window used by the “Competing this weekend” feature. The window is calculated in the configured attendance timezone. The separate WCA Live verification experiment retains its independently configured collection dates.
_Avoid_: Calendar week

**Attendance**:
A returning competitor's presence on a competition's public accepted competitor list. Returning competitors have a WCA ID; first-time competitors are intentionally excluded. CubeRecord presents Attendance directly without implying guaranteed physical participation.
_Avoid_: Confirmed attendance

**Result Value**:
The integer-encoded result received from an upstream live-results provider and preserved without
display conversion.
_Avoid_: Display time

**Display Value**:
The human-readable representation of a Result Value according to the event's WCA formatting rules.
_Avoid_: Raw value

**Source Observation**:
A provider payload observed by CubingNow at a particular moment and retained as raw evidence. Multiple Source Observations may describe changes to the same Provider Result State.
_Avoid_: Record, Canonical Result

**Ingestion Run**:
A bounded subscription or reconciliation activity that receives Source Observations and processes them into CubingNow's current domain data.
_Avoid_: Import, sync

**Classification Pass**:
One deterministic recalculation of the Achievements and Qualification Decisions for all current Canonical Results in a single event and result kind. It reads committed facts and stored benchmarks; it does not poll an external provider.
_Avoid_: Ingestion, API poll

**Classification Replay**:
The chronological calculation inside a Classification Pass. It starts from historical record and personal-best baselines, then walks current results in entry order so corrections and retractions can change later classifications correctly.
_Avoid_: Provider replay, message retry

**Dirty Classification Scope**:
A durable, versioned request saying that one event and result kind must receive a new Classification Pass because finalized facts changed. Multiple changes in one ingestion transaction create one version increment for that scope.
_Avoid_: WebSocket queue item, result observation

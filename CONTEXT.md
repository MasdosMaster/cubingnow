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
The single competition result to which observations from one or more providers refer. Corrections revise this identity rather than creating an unrelated result.
_Avoid_: Source result, record row

**Source Claim**:
A provider's assertion that an observed result has a particular record label. A Source Claim is evidence and is not CubingNow's classification.
_Avoid_: Achievement, verified record

**Qualification Decision**:
CubingNow's decision about whether an Achievement appears publicly or is eligible for notification. It is distinct from the Achievement itself.
_Avoid_: Classification, valid flag

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
An immutable payload observed by CubingNow from an external source at a particular moment. Multiple Source Observations may describe changes to the same Canonical Result.
_Avoid_: Record, Canonical Result

**Ingestion Run**:
A bounded subscription or reconciliation activity that receives Source Observations and processes them into CubingNow's current domain data.
_Avoid_: Import, sync

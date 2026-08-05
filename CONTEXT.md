# CubingNow

CubingNow presents newly detected official speedcubing records and highlights notable competitors attending upcoming competitions.

## Language

**Record**:
An upstream result marked as a world, continental, or national record, for either a single result
or an average. A Record may later be corrected or withdrawn when its source result changes.

**Record Level**:
The single, highest geographic designation assigned to a Record: world (WR), continental (CR), or national (NR). Record Levels are mutually exclusive; a Record never cascades into lower-level categories.
_Avoid_: Record type

**Detection Time**:
The moment one CubingNow ingestion pipeline first observes a Record in its external source. It is
not necessarily the solve time, especially when the source supplies no entry timestamp.
_Avoid_: Record time, creation time

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
The human-readable representation of a Result Value according to the event's WCA formatting rules. Until formatting is implemented, it is the string form of the unchanged Result Value.
_Avoid_: Raw value

**Source Observation**:
An immutable payload observed by CubingNow from an external source at a particular moment. Multiple Source Observations may describe changes to the same Result.
_Avoid_: Record, when referring to an unprocessed external message

**Ingestion Run**:
A bounded subscription or reconciliation activity that receives Source Observations and processes them into CubingNow's current domain data.
_Avoid_: Import, sync

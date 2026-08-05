RECENT_RECORDS_QUERY = """
query CubeRecordRecentRecords {
  recentRecords {
    id
    type
    tag
    attemptResult
    result {
      id
      best
      average
      enteredAt
      person {
        id
        wcaId
        name
        country { iso2 }
      }
      round {
        id
        number
        name
        competitionEvent {
          event { id name }
          competition {
            id
            wcaId
            name
            startDate
            endDate
            venues {
              name
              timezone
              country { iso2 }
            }
          }
        }
      }
    }
  }
}
"""


# WCA Live has no cursor/offset argument. Omitting `limit` is the only schema-supported
# complete fetch. The nested event/round fetch is deliberately split out because the
# combined document exceeds WCA Live's deployed max GraphQL complexity of 5000.
WEEKEND_COMPETITIONS_QUERY = """
query CubeRecordWeekendCompetitions($from: Date!) {
  competitions(from: $from) {
    id
    wcaId
    name
    startDate
    endDate
  }
}
"""


COMPETITION_ROUNDS_QUERY = """
query CubeRecordCompetitionRounds($id: ID!) {
  competition(id: $id) {
    id
    wcaId
    name
    startDate
    endDate
    competitionEvents {
      event { id name }
      rounds { id number name }
    }
  }
}
"""


ROUND_UPDATED_SUBSCRIPTION = """
subscription CubeRecordRoundUpdated($id: ID!) {
  roundUpdated(id: $id) {
    id
    results {
      id
      ranking
      attempts { result }
      best
      average
      singleRecordTag
      averageRecordTag
      enteredAt
      person {
        id
        wcaId
        name
        country { iso2 }
      }
    }
  }
}
"""

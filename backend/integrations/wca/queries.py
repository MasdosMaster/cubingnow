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
        wcaId
        name
        country { iso2 }
      }
      round {
        competitionEvent {
          event { id name }
          competition {
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


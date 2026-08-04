function flag(code) {
  return code.toUpperCase().replace(/./g, (character) =>
    String.fromCodePoint(127397 + character.charCodeAt())
  );
}

export function RecordList({ records }) {
  if (!records.length) {
    return <div className="empty"><h2>No records found</h2><p>Collected records will appear here.</p></div>;
  }
  return (
    <div className="record-list">
      {records.map((record) => (
        <article className="record-row" key={record.id}>
          <span className={`level level-${record.level.toLowerCase()}`}>{record.level}</span>
          <div><strong>{record.competitor.name}</strong><small>{flag(record.competitor.country_code)} {record.competitor.wca_id}</small></div>
          <div><strong>{record.event_name}</strong><small>{record.result_kind}</small></div>
          <strong className="result">{record.display_value}</strong>
          <div><strong>{record.competition.name}</strong><small>{record.competition.city}</small></div>
        </article>
      ))}
    </div>
  );
}


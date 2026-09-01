export default function SimPanel({ onRun }) {
  return (
    <div className="sim">
      <h3>Fault simulator</h3>
      <div className="grid">
        <button onClick={() => onRun('span')}>Span fault</button>
        <button onClick={() => onRun('weakSpan')}>Weak span</button>
        <button onClick={() => onRun('dt')}>DT fault</button>
        <button onClick={() => onRun('feeder')}>Feeder fault</button>
        <button onClick={() => onRun('deadSensor')}>Dead sensor</button>
        <button onClick={() => onRun('scheduled')}>Sched. outage</button>
        <button onClick={() => onRun('noise')}>Dup / late msg</button>
        <button className="full" onClick={() => onRun('reset')}>↺ Reset to all-live</button>
      </div>
    </div>
  )
}
